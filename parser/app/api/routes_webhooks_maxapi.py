"""Webhook receiver for the MAX gateway.

The MAX gateway delivers ``PublicationEvent`` payloads to a single
endpoint configured by the operator. The parser exposes that endpoint
here and translates each event into the parser's existing internal
publication-state APIs:

* ``publication.published`` → :func:`Repository.mark_post_published`
* ``publication.failed`` → :func:`Repository.mark_post_failed` (retryable)
* ``publication.cancelled`` → :func:`Repository.mark_post_failed` (non-retryable)
* ``publication.expired`` → :func:`Repository.mark_post_failed` (non-retryable)
* ``metrics.collected`` → :func:`Repository.add_publication_metrics`

``publication.accepted`` and ``publication.scheduled`` are accepted
silently because they describe lifecycle states the parser doesn't
track.

When ``MAXAPI_WEBHOOK_SECRET`` is configured, every delivery is
authenticated with HMAC-SHA256 over ``event_id + "." + occurred_at``
(the algorithm documented in ``maxapi/openapi.yml``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.repositories import Repository
from app.db.session import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_iso_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid_datetime: {value!r}"
            ) from exc
        return _as_utc(parsed)
    raise HTTPException(status_code=400, detail="missing_datetime")


def _verify_signature(
    *,
    secret: str,
    event_id: str,
    occurred_at: str,
    provided_signature: str | None,
    body_signature: str | None,
) -> None:
    if not secret:
        return
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{event_id}.{occurred_at}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    candidate = provided_signature or body_signature
    if candidate is None or not hmac.compare_digest(expected, candidate):
        log.warning("rejected maxapi webhook with invalid signature for event %s", event_id)
        raise HTTPException(status_code=401, detail="invalid_signature")


def _extract_post_id(job: dict[str, Any]) -> str:
    ready_post = job.get("ready_post") or {}
    post_id = ready_post.get("post_id")
    if post_id is None:
        raise HTTPException(status_code=400, detail="missing_post_id_in_event")
    return str(post_id)


def _handle_published(session: Session, event: dict[str, Any]) -> None:
    job = event.get("job") or {}
    post_id = _extract_post_id(job)
    published = job.get("published_post") or {}
    message_id = published.get("message_id") or job.get("job_id")
    if message_id is None:
        raise HTTPException(status_code=400, detail="missing_message_id_in_event")
    permalink = published.get("permalink")
    published_at = _parse_iso_datetime(
        published.get("published_at") or job.get("updated_at") or event.get("occurred_at")
    )
    Repository.mark_post_published(
        session=session,
        post_id=post_id,
        telegram_message_id=str(message_id),
        telegram_url=permalink,
        published_at=published_at,
    )


def _handle_failed(
    session: Session, event: dict[str, Any], *, retryable: bool, fallback_code: str
) -> None:
    job = event.get("job") or {}
    post_id = _extract_post_id(job)
    error = job.get("error") or {}
    code = str(error.get("code") or fallback_code)
    message = str(error.get("message") or "")
    Repository.mark_post_failed(
        session=session,
        post_id=post_id,
        retryable=retryable,
        error_message=f"{code}: {message}" if message else code,
    )


def _handle_metrics(session: Session, event: dict[str, Any]) -> None:
    job = event.get("job") or {}
    post_id = _extract_post_id(job)
    metrics = event.get("metrics") or {}
    Repository.add_publication_metrics(
        session=session,
        post_id=post_id,
        collected_at=_parse_iso_datetime(metrics.get("collected_at") or event.get("occurred_at")),
        views_count=int(metrics.get("views_count") or 0),
        reactions_total=int(metrics.get("reactions_total") or 0),
        reaction_breakdown={
            str(k): int(v) for k, v in (metrics.get("reaction_breakdown") or {}).items()
        },
        comments_count=int(metrics.get("comments_count") or 0),
        forwards_count=int(metrics.get("forwards_count") or 0),
    )


@router.post("/maxapi")
async def receive_maxapi_event(
    request: Request,
    session: Session = Depends(get_session),
    x_maxapi_signature: str | None = Header(default=None, alias="X-Maxapi-Signature"),
) -> dict[str, str]:
    settings = get_settings()
    event = await request.json()
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="event_must_be_object")

    event_id = str(event.get("event_id") or "")
    event_type = str(event.get("event_type") or "")
    occurred_at_raw = event.get("occurred_at")
    occurred_at_str = (
        occurred_at_raw if isinstance(occurred_at_raw, str) else (occurred_at_raw or "")
    )
    if not event_id or not event_type or not occurred_at_str:
        raise HTTPException(status_code=400, detail="missing_required_event_fields")

    _verify_signature(
        secret=settings.MAXAPI_WEBHOOK_SECRET,
        event_id=event_id,
        occurred_at=str(occurred_at_str),
        provided_signature=x_maxapi_signature,
        body_signature=event.get("signature"),
    )

    if event_type == "publication.published":
        _handle_published(session, event)
    elif event_type == "publication.failed":
        _handle_failed(session, event, retryable=True, fallback_code="publication_failed")
    elif event_type == "publication.cancelled":
        _handle_failed(session, event, retryable=False, fallback_code="publication_cancelled")
    elif event_type == "publication.expired":
        _handle_failed(session, event, retryable=False, fallback_code="publication_expired")
    elif event_type == "metrics.collected":
        _handle_metrics(session, event)
    elif event_type in {"publication.accepted", "publication.scheduled"}:
        # The parser does not track these intermediate states; ack them.
        log.debug("acknowledging maxapi event %s (type=%s)", event_id, event_type)
    else:
        log.info("ignoring unknown maxapi event type=%s", event_type)
        return {"status": "ignored", "event_type": event_type}

    session.commit()
    return {"status": "ok", "event_type": event_type}
