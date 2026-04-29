"""Tests for ``daily_planner`` and the admin REST endpoints that wrap it."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./.test_wb_parser.db")

from app.config import get_settings  # noqa: E402
from app.db.models import Post, Product  # noqa: E402
from app.db.repositories import Repository  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.services.daily_planner import (  # noqa: E402
    DailyPlannerService,
    SlotSpec,
    _slot_to_utc,
)
from app.utils.time import utcnow  # noqa: E402


@pytest.fixture(autouse=True)
def _initialize_db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/wb.db"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    from app.db import session as session_mod

    session_mod._engine = None  # type: ignore[attr-defined]
    init_db()
    yield


def _make_product(session, article_id: str) -> Product:
    product = Product(
        article_id=article_id,
        title=f"Product {article_id}",
        normalized_title=f"product {article_id}",
        product_url=f"https://wildberries.ru/catalog/{article_id}/detail.aspx",
        canonical_url=f"https://wildberries.ru/catalog/{article_id}/detail.aspx",
        image_urls=[f"https://images.wb.ru/{article_id}-1.jpg"],
        availability=True,
    )
    session.add(product)
    session.flush()
    return product


def _make_ready_post(
    session,
    *,
    post_id: str,
    post_type: str = "collection",
    article_id: str = "100",
    planned_at: datetime | None = None,
    fresh_for_days: int = 1,
) -> Post:
    _make_product(session, article_id)
    post = Repository.upsert_post(
        session=session,
        post_id=post_id,
        post_type=post_type,
        title=f"Title {post_id}",
        text=f"Text for {post_id}",
        media_urls=[
            {"position": 1, "url": f"https://img/{article_id}.jpg", "type": "photo"}
        ],
        parse_mode=None,
        fresh_until=utcnow() + timedelta(days=fresh_for_days),
        planned_at=planned_at,
        publication_status="ready",
    )
    Repository.replace_post_items(
        session,
        post.id,
        [
            {
                "position": 1,
                "article_id": article_id,
                "displayed_url": f"https://wildberries.ru/catalog/{article_id}/detail.aspx",
                "price": 999,
                "old_price": None,
                "discount_percent": None,
                "caption": None,
            }
        ],
    )
    return post


def test_slot_to_utc_moscow():
    slot = _slot_to_utc(date(2026, 5, 1), "10:00", "Europe/Moscow")
    # Moscow is UTC+3 → 10:00 MSK == 07:00 UTC
    assert slot == datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc)


def test_plan_day_assigns_slots_to_unplanned_ready_posts():
    settings = get_settings()
    with session_scope() as session:
        _make_ready_post(session, post_id="c-1", post_type="collection", article_id="100")
        _make_ready_post(session, post_id="c-2", post_type="collection", article_id="101")
        _make_ready_post(session, post_id="c-3", post_type="collection", article_id="102")
        _make_ready_post(session, post_id="s-1", post_type="single", article_id="201")
        _make_ready_post(session, post_id="s-2", post_type="single", article_id="202")

    planner = DailyPlannerService(settings)
    slots = [
        SlotSpec(time="10:00", type="collection"),
        SlotSpec(time="12:00", type="single"),
        SlotSpec(time="14:00", type="collection", with_reaction_poll=True),
        SlotSpec(time="16:00", type="collection"),
        SlotSpec(time="19:00", type="single"),
    ]
    target = date(2026, 5, 1)
    with session_scope() as session:
        plan = planner.plan_day(session, slots, target_date=target, tz_name="Europe/Moscow")

    assert len(plan.slots) == 5
    assert plan.skipped_slots == []
    types_in_order = [sp.slot.type for sp in plan.slots]
    assert types_in_order == ["collection", "single", "collection", "collection", "single"]

    with session_scope() as session:
        for slot_plan in plan.slots:
            post = Repository.get_post(session, slot_plan.post_id)
            assert post is not None
            assert post.planned_at is not None
            # planned_at should be exactly on the slot time in UTC
            expected = _slot_to_utc(target, slot_plan.slot.time, "Europe/Moscow")
            assert post.planned_at.replace(tzinfo=timezone.utc) if post.planned_at.tzinfo is None else post.planned_at == expected


def test_plan_day_reuses_existing_slot_assignment():
    settings = get_settings()
    target = date(2026, 5, 1)
    slot = SlotSpec(time="10:00", type="collection")
    with session_scope() as session:
        _make_ready_post(session, post_id="c-1", post_type="collection", article_id="100")
        _make_ready_post(session, post_id="c-2", post_type="collection", article_id="101")

    planner = DailyPlannerService(settings)
    with session_scope() as session:
        first = planner.plan_day(session, [slot], target_date=target, tz_name="Europe/Moscow")
    with session_scope() as session:
        second = planner.plan_day(session, [slot], target_date=target, tz_name="Europe/Moscow")
        slot_at = _slot_to_utc(target, slot.time, "Europe/Moscow").replace(tzinfo=None)
        assigned = [
            post.id
            for post in session.query(Post)
            .filter(Post.planned_at == slot_at)
            .filter(Post.publication_status == "ready")
            .all()
        ]

    assert first.slots[0].post_id == second.slots[0].post_id
    assert assigned == [first.slots[0].post_id]


def test_plan_day_skips_when_pool_empty():
    settings = get_settings()
    with session_scope() as session:
        _make_ready_post(session, post_id="c-1", post_type="collection", article_id="100")

    planner = DailyPlannerService(settings)
    slots = [
        SlotSpec(time="10:00", type="collection"),
        SlotSpec(time="12:00", type="single"),
    ]
    with session_scope() as session:
        plan = planner.plan_day(session, slots, target_date=date(2026, 5, 1))

    assert len(plan.slots) == 1
    assert plan.slots[0].slot.type == "collection"
    assert len(plan.skipped_slots) == 1
    assert plan.skipped_slots[0]["type"] == "single"


def test_get_next_ready_posts_filters_planned_at_in_future():
    """Posts whose planned_at is still in the future must NOT be returned."""
    future = utcnow() + timedelta(hours=2)
    past = utcnow() - timedelta(minutes=5)
    with session_scope() as session:
        _make_ready_post(
            session, post_id="future", post_type="collection",
            article_id="900", planned_at=future,
        )
        _make_ready_post(
            session, post_id="now", post_type="collection",
            article_id="901", planned_at=past,
        )
        _make_ready_post(
            session, post_id="unplanned", post_type="collection",
            article_id="902", planned_at=None,
        )

    with session_scope() as session:
        ready = Repository.get_next_ready_posts(session, limit=10)

    ids = [p.id for p in ready]
    assert "future" not in ids
    assert "now" in ids
    assert "unplanned" in ids
    # planned_at-asc nulls last → "now" comes before "unplanned"
    assert ids.index("now") < ids.index("unplanned")
