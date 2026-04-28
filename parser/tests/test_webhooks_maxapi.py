from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import get_settings
from app.db.repositories import Repository
from app.db.session import session_scope
from app.services.normalize_product import normalize_wb_product
from app.utils.time import utcnow


def _seed_locked_post() -> str:
    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "wb_product.json").read_text(encoding="utf-8")
    )
    parsed = normalize_wb_product(raw, source="search")
    with session_scope() as session:
        Repository.upsert_product(session, parsed)
        post_id = Repository.generate_post_id("single", [parsed.article_id], bucket="webhook-test")
        post = Repository.upsert_post(
            session=session,
            post_id=post_id,
            post_type="single",
            title="webhook test",
            text="t",
            media_urls=[
                {"position": 1, "article_id": parsed.article_id, "url": parsed.image_urls[0]}
            ],
            parse_mode=None,
            fresh_until=utcnow() + timedelta(hours=12),
            planned_at=None,
            publication_status="ready",
        )
        Repository.replace_post_items(
            session,
            post.id,
            [
                {
                    "position": 1,
                    "article_id": parsed.article_id,
                    "title": parsed.title,
                    "price": parsed.price,
                    "displayed_url": parsed.canonical_url,
                    "caption": "reason",
                }
            ],
        )
        Repository.lock_post(
            session=session,
            post_id=post_id,
            worker_id="bridge-test",
            ttl_seconds=600,
        )
    return post_id


def _publication_event(
    event_type: str,
    *,
    post_id: str,
    job_id: str = "job_x",
    message_id: str = "msg_42",
    permalink: str = "https://max.ru/c/test/42",
    error: dict | None = None,
    metrics: dict | None = None,
) -> dict:
    occurred_at = datetime.now(timezone.utc).isoformat()
    job: dict = {
        "job_id": job_id,
        "account_id": "acc_DEMO",
        "channel_id": "ch_DEMO",
        "status": (
            "published"
            if event_type == "publication.published"
            else "failed"
            if event_type in {"publication.failed", "publication.expired"}
            else "cancelled"
            if event_type == "publication.cancelled"
            else "scheduled"
        ),
        "ready_post": {"post_id": post_id},
        "attempts": 1,
        "created_at": occurred_at,
        "updated_at": occurred_at,
    }
    if event_type == "publication.published":
        job["published_post"] = {
            "message_id": message_id,
            "permalink": permalink,
            "published_at": occurred_at,
        }
    if error is not None:
        job["error"] = error
    payload: dict = {
        "event_id": "evt_test",
        "event_type": event_type,
        "occurred_at": occurred_at,
        "job": job,
    }
    if metrics is not None:
        payload["metrics"] = metrics
    return payload


def test_published_event_marks_post_published(test_env):
    post_id = _seed_locked_post()
    client = TestClient(create_app())

    event = _publication_event("publication.published", post_id=post_id)
    response = client.post("/api/v1/webhooks/maxapi", json=event)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "event_type": "publication.published"}

    with session_scope() as session:
        post = Repository.get_post(session, post_id)
        assert post is not None
        assert post.publication_status == "published"
        assert post.telegram_message_id == "msg_42"


def test_failed_event_returns_post_to_ready_for_retry(test_env):
    """`publication.failed` is treated as retryable: parser puts the post back
    into the `ready` queue and clears the worker lock."""
    post_id = _seed_locked_post()
    client = TestClient(create_app())

    event = _publication_event(
        "publication.failed",
        post_id=post_id,
        error={"code": "upstream_unavailable", "message": "MAX timed out"},
    )
    response = client.post("/api/v1/webhooks/maxapi", json=event)

    assert response.status_code == 200
    with session_scope() as session:
        post = Repository.get_post(session, post_id)
        assert post is not None
        assert post.publication_status == "ready"
        assert post.lock_worker_id is None


def test_cancelled_event_marks_post_failed_non_retryable(test_env):
    post_id = _seed_locked_post()
    client = TestClient(create_app())

    event = _publication_event("publication.cancelled", post_id=post_id)
    response = client.post("/api/v1/webhooks/maxapi", json=event)

    assert response.status_code == 200
    with session_scope() as session:
        post = Repository.get_post(session, post_id)
        assert post is not None
        assert post.publication_status == "failed"


def test_metrics_event_records_metrics(test_env):
    post_id = _seed_locked_post()
    client = TestClient(create_app())

    metrics = {
        "message_id": "msg_42",
        "views_count": 1000,
        "reactions_total": 200,
        "reaction_breakdown": {"❤️": 150, "🔥": 50},
        "comments_count": 5,
        "forwards_count": 3,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    event = _publication_event("metrics.collected", post_id=post_id, metrics=metrics)

    response = client.post("/api/v1/webhooks/maxapi", json=event)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "event_type": "metrics.collected"}


def test_intermediate_events_are_acknowledged(test_env):
    post_id = _seed_locked_post()
    client = TestClient(create_app())

    for event_type in ("publication.accepted", "publication.scheduled"):
        event = _publication_event(event_type, post_id=post_id)
        response = client.post("/api/v1/webhooks/maxapi", json=event)
        assert response.status_code == 200, event_type
        assert response.json()["status"] == "ok"

    # The post must NOT have been marked published or failed by these events.
    with session_scope() as session:
        post = Repository.get_post(session, post_id)
        assert post is not None
        assert post.publication_status not in {"published", "failed"}


def test_signature_required_when_secret_configured(test_env, monkeypatch):
    monkeypatch.setenv("MAXAPI_WEBHOOK_SECRET", "supersecret-12345678")
    get_settings.cache_clear()

    post_id = _seed_locked_post()
    client = TestClient(create_app())

    event = _publication_event("publication.published", post_id=post_id)

    # No signature → 401.
    bad = client.post("/api/v1/webhooks/maxapi", json=event)
    assert bad.status_code == 401

    # Wrong signature → 401.
    bad2 = client.post(
        "/api/v1/webhooks/maxapi",
        json=event,
        headers={"X-Maxapi-Signature": "deadbeef"},
    )
    assert bad2.status_code == 401

    # Correct signature → 200.
    expected = hmac.new(
        b"supersecret-12345678",
        f"{event['event_id']}.{event['occurred_at']}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    good = client.post(
        "/api/v1/webhooks/maxapi",
        json=event,
        headers={"X-Maxapi-Signature": expected},
    )
    assert good.status_code == 200


def test_unknown_event_type_is_ignored(test_env):
    post_id = _seed_locked_post()
    client = TestClient(create_app())

    event = _publication_event("publication.published", post_id=post_id)
    event["event_type"] = "publication.somethingnew"
    response = client.post("/api/v1/webhooks/maxapi", json=event)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_missing_required_fields_returns_400(test_env):
    client = TestClient(create_app())
    response = client.post("/api/v1/webhooks/maxapi", json={"event_type": "publication.published"})
    assert response.status_code == 400
