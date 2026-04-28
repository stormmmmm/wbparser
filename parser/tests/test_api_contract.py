from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.db.repositories import Repository
from app.db.session import session_scope
from app.services.normalize_product import normalize_wb_product
from app.utils.time import utcnow


def test_api_contract_next_lock_published(test_env):
    raw = json.loads((Path(__file__).parent / "fixtures" / "wb_product.json").read_text(encoding="utf-8"))
    parsed = normalize_wb_product(raw, source="search")

    with session_scope() as session:
        Repository.upsert_product(session, parsed)
        post_id = Repository.generate_post_id("single", [parsed.article_id], bucket="test")
        post = Repository.upsert_post(
            session=session,
            post_id=post_id,
            post_type="single",
            title="Тестовый пост",
            text=f"{parsed.article_id} - {parsed.price} ₽",
            media_urls=[{"position": 1, "article_id": parsed.article_id, "url": parsed.image_urls[0]}],
            parse_mode=None,
            fresh_until=utcnow() + timedelta(minutes=30),
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
                    "old_price": parsed.old_price,
                    "discount_percent": parsed.discount_percent,
                    "displayed_url": parsed.canonical_url,
                    "caption": "reason",
                }
            ],
        )

    client = TestClient(create_app())

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    next_posts = client.get("/api/v1/posts/next", params={"limit": 1})
    assert next_posts.status_code == 200
    body = next_posts.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["post_id"] == post_id

    lock_response = client.post(
        f"/api/v1/posts/{post_id}/lock",
        json={"worker_id": "poster-1", "lock_ttl_seconds": 600},
    )
    assert lock_response.status_code == 200
    assert lock_response.json()["publication_status"] == "locked"

    published_response = client.post(
        f"/api/v1/posts/{post_id}/published",
        json={
            "telegram_message_id": "777",
            "telegram_url": "https://t.me/channel/777",
            "published_at": utcnow().isoformat(),
            "raw_response": {},
        },
    )
    assert published_response.status_code == 200
    assert published_response.json()["publication_status"] == "published"
