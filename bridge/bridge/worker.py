"""The publication loop that ties the parser and the MAX gateway together.

The worker is intentionally small — every single thing it does is one of:

1. ``GET  parser /api/v1/posts/next``   — pull candidate(s).
2. ``POST parser /api/v1/posts/{id}/lock`` — claim the post.
3. ``POST maxapi /v1/accounts/{acc}/publication-jobs`` — submit the job.
4. ``POST parser /api/v1/posts/{id}/published`` *or* ``/failed`` — report.

Anything that resembles selection, scoring, rewriting, scheduling
heuristics, retry policy beyond a simple retryable/non-retryable split,
or media transformations belongs to one of the upstream services, not
here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from bridge.maxapi_client import MaxApiClient
from bridge.parser_client import ParserClient
from bridge.translator import build_publication_job_request

log = logging.getLogger(__name__)


def _parse_iso_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _classify_failure(exc: Exception) -> tuple[str, str, bool]:
    """Map an exception raised by the gateway call to ``(code, message, retryable)``."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retryable = status >= 500 or status == 429
        try:
            payload = exc.response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            code = (
                payload.get("code")
                or payload.get("error", {}).get("code")  # type: ignore[union-attr]
                or f"http_{status}"
            )
            message = (
                payload.get("message")
                or payload.get("error", {}).get("message")  # type: ignore[union-attr]
                or exc.response.text
            )
        else:
            code = f"http_{status}"
            message = exc.response.text
        return str(code), str(message), retryable
    if isinstance(exc, httpx.HTTPError):
        return "transport_error", str(exc) or exc.__class__.__name__, True
    return "bridge_internal_error", f"{exc.__class__.__name__}: {exc}", False


async def process_one(
    ready_post: dict[str, Any],
    *,
    parser: ParserClient,
    maxapi: MaxApiClient,
    account_id: str,
    channel_id: str,
    worker_id: str,
    lock_ttl_seconds: int,
) -> str:
    """Run the full publication pipeline for a single ``ReadyPost``.

    Returns one of ``"published"``, ``"locked_by_other"``, ``"failed"``.
    """

    post_id = ready_post.get("post_id")
    if post_id is None:
        log.warning("Skipping ReadyPost without post_id")
        return "failed"
    post_id_str = str(post_id)

    locked = await parser.lock_post(
        post_id_str, worker_id=worker_id, lock_ttl_seconds=lock_ttl_seconds
    )
    if locked is None:
        log.info("post %s is already locked elsewhere, skipping", post_id_str)
        return "locked_by_other"

    body = build_publication_job_request(locked, channel_id=channel_id)
    try:
        job = await maxapi.create_publication_job(
            account_id=account_id,
            body=body,
            idempotency_key=f"wb-bridge:{post_id_str}",
        )
    except Exception as exc:  # noqa: BLE001 — we re-classify and report.
        code, message, retryable = _classify_failure(exc)
        log.warning(
            "publish failed for post %s: %s (%s, retryable=%s)",
            post_id_str,
            message,
            code,
            retryable,
        )
        await parser.mark_failed(
            post_id_str, error_code=code, error_message=message, retryable=retryable
        )
        return "failed"

    status = job.get("status")
    if status not in {"published", "scheduled"}:
        # Treat unexpected gateway states (e.g. failed) as a publish failure.
        error = job.get("error") or {}
        code = str(error.get("code") or f"job_status_{status}")
        message = str(error.get("message") or f"unexpected job status: {status}")
        log.warning("post %s landed in status %s: %s", post_id_str, status, message)
        await parser.mark_failed(
            post_id_str, error_code=code, error_message=message, retryable=False
        )
        return "failed"

    if status == "scheduled":
        # The gateway has accepted the post for later publication.
        # Reporting back to the parser will happen via the webhook
        # receiver when the gateway eventually publishes / fails it.
        log.info("post %s scheduled (job_id=%s)", post_id_str, job.get("job_id"))
        return "scheduled"

    published = job.get("published_post") or {}
    message_id = str(published.get("message_id") or job.get("job_id"))
    permalink = published.get("permalink")
    published_at = _parse_iso_datetime(
        published.get("published_at") or job.get("updated_at") or job.get("created_at")
    )

    await parser.mark_published(
        post_id_str,
        message_id=message_id,
        message_url=permalink,
        published_at=published_at,
        raw_response=job,
    )
    log.info("post %s published (job_id=%s, message_id=%s)", post_id_str, job.get("job_id"), message_id)
    return "published"


async def run_once(
    *,
    parser: ParserClient,
    maxapi: MaxApiClient,
    account_id: str,
    channel_id: str,
    worker_id: str,
    lock_ttl_seconds: int,
    batch_size: int,
    post_type: str | None = None,
) -> list[str]:
    """Run a single publication cycle. Returns per-post outcomes."""
    ready_posts = await parser.get_next_posts(limit=batch_size, post_type=post_type)
    outcomes: list[str] = []
    for ready_post in ready_posts:
        outcome = await process_one(
            ready_post,
            parser=parser,
            maxapi=maxapi,
            account_id=account_id,
            channel_id=channel_id,
            worker_id=worker_id,
            lock_ttl_seconds=lock_ttl_seconds,
        )
        outcomes.append(outcome)
    return outcomes


async def run_loop(
    *,
    parser: ParserClient,
    maxapi: MaxApiClient,
    account_id: str,
    channel_id: str,
    worker_id: str,
    lock_ttl_seconds: int,
    batch_size: int,
    poll_interval_seconds: float,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the publication loop until ``stop_event`` is set."""
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        outcomes = await run_once(
            parser=parser,
            maxapi=maxapi,
            account_id=account_id,
            channel_id=channel_id,
            worker_id=worker_id,
            lock_ttl_seconds=lock_ttl_seconds,
            batch_size=batch_size,
        )
        if not outcomes:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
            except TimeoutError:
                pass
