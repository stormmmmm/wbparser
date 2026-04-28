"""End-to-end test of the bridge worker against real upstream apps.

This test wires both the WB parser and the MAX gateway through
``httpx.ASGITransport`` (no sockets), seeds a single ``ReadyPost`` in
the parser, and asserts that one ``process_one`` cycle:

* locks the post in the parser,
* creates a publication job in the gateway with ``source="wb_parser"``,
* and reports the post back to the parser as ``published``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from bridge.maxapi_client import MaxApiClient
from bridge.parser_client import ParserClient
from bridge.worker import process_one

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "parser"
    / "tests"
    / "fixtures"
    / "wb_product.json"
)


def _seed_ready_post() -> str:
    """Insert one ready post in the parser DB and return its post_id."""
    from app.db.repositories import Repository
    from app.db.session import session_scope
    from app.services.normalize_product import normalize_wb_product
    from app.utils.time import utcnow

    raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    parsed = normalize_wb_product(raw, source="search")

    with session_scope() as session:
        Repository.upsert_product(session, parsed)
        post_id = Repository.generate_post_id("single", [parsed.article_id], bucket="bridge-e2e")
        post = Repository.upsert_post(
            session=session,
            post_id=post_id,
            post_type="single",
            title="Bridge E2E test post",
            text=f"{parsed.article_id} — {parsed.price} ₽",
            media_urls=[
                {
                    "position": 1,
                    "article_id": parsed.article_id,
                    "url": parsed.image_urls[0],
                }
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
                    "old_price": parsed.old_price,
                    "discount_percent": parsed.discount_percent,
                    "displayed_url": parsed.canonical_url,
                    "caption": "reason",
                }
            ],
        )
    return post_id


@pytest.mark.asyncio
async def test_bridge_publishes_one_ready_post(parser_env, maxapi_env):
    from api.main import create_app as create_maxapi_app
    from app.api.main import create_app as create_parser_app
    from app.db.models import Post
    from app.db.session import session_scope

    post_id = _seed_ready_post()

    parser_app = create_parser_app()
    maxapi_app = create_maxapi_app()

    parser_transport = httpx.ASGITransport(app=parser_app)
    maxapi_transport = httpx.ASGITransport(app=maxapi_app)

    async with (
        httpx.AsyncClient(transport=parser_transport, base_url="http://parser") as parser_http,
        httpx.AsyncClient(transport=maxapi_transport, base_url="http://maxapi") as maxapi_http,
    ):
        parser_client = ParserClient(parser_http)
        maxapi_client = MaxApiClient(maxapi_http, token="test-token")

        # Pull the seeded post via the real /next endpoint.
        ready_posts = await parser_client.get_next_posts(limit=1)
        assert len(ready_posts) == 1
        assert ready_posts[0]["post_id"] == post_id
        assert ready_posts[0]["source"] == "wb_parser"

        # And run a single bridge cycle against both real services.
        outcome = await process_one(
            ready_posts[0],
            parser=parser_client,
            maxapi=maxapi_client,
            account_id="acc_DEMO0000000000000000000000",
            channel_id="-1001111111111",
            worker_id="bridge-test",
            lock_ttl_seconds=60,
        )

    assert outcome == "published"

    # The parser is now up to date with the publication.
    with session_scope() as session:
        published = session.get(Post, post_id)
        assert published is not None
        assert published.publication_status == "published"
        assert published.telegram_message_id is not None


@pytest.mark.asyncio
async def test_bridge_marks_failed_when_gateway_rejects(parser_env, maxapi_env):
    from api.main import create_app as create_maxapi_app
    from app.api.main import create_app as create_parser_app
    from app.db.models import Post
    from app.db.session import session_scope

    post_id = _seed_ready_post()

    parser_app = create_parser_app()
    maxapi_app = create_maxapi_app()

    parser_transport = httpx.ASGITransport(app=parser_app)
    maxapi_transport = httpx.ASGITransport(app=maxapi_app)

    async with (
        httpx.AsyncClient(transport=parser_transport, base_url="http://parser") as parser_http,
        httpx.AsyncClient(transport=maxapi_transport, base_url="http://maxapi") as maxapi_http,
    ):
        parser_client = ParserClient(parser_http)
        maxapi_client = MaxApiClient(maxapi_http, token="test-token")

        ready_posts = await parser_client.get_next_posts(limit=1)
        assert len(ready_posts) == 1

        # Unknown channel → gateway returns 404 channel_not_found.
        outcome = await process_one(
            ready_posts[0],
            parser=parser_client,
            maxapi=maxapi_client,
            account_id="acc_DEMO0000000000000000000000",
            channel_id="this-channel-does-not-exist",
            worker_id="bridge-test",
            lock_ttl_seconds=60,
        )

    assert outcome == "failed"
    with session_scope() as session:
        post = session.get(Post, post_id)
        assert post is not None
        assert post.publication_status == "failed"
