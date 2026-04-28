"""Admin-facing endpoints used by the wbpost CLI."""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Post
from app.db.repositories import Repository
from app.db.session import get_session
from app.services.collect_candidates import CandidateCollectorService
from app.services.daily_planner import DEFAULT_TIMEZONE, DailyPlannerService, SlotSpec
from app.services.post_builder import PostBuilderService
from app.services.score_products import ScoringService
from app.utils.time import utcnow

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class SlotPayload(BaseModel):
    time: str
    type: str
    with_reaction_poll: bool = False


class PlanDayIn(BaseModel):
    date: str | None = Field(None, description="YYYY-MM-DD; default = tomorrow in tz")
    timezone: str = DEFAULT_TIMEZONE
    slots: list[SlotPayload]


class PostOnceIn(BaseModel):
    post_type: str = "collection"
    with_reaction_poll: bool = False


def _parse_date(raw: str | None) -> date_type | None:
    if not raw:
        return None
    return date_type.fromisoformat(raw)


@router.post("/plan-day")
def plan_day(payload: PlanDayIn, session: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    if not payload.slots:
        raise HTTPException(status_code=422, detail="slots must be non-empty")
    target = _parse_date(payload.date)
    spec = [SlotSpec(**slot.model_dump()) for slot in payload.slots]
    planner = DailyPlannerService(settings)
    plan = planner.plan_day(session, spec, target_date=target, tz_name=payload.timezone)
    session.commit()
    return plan.to_dict()


@router.post("/post-once")
def post_once(payload: PostOnceIn, session: Session = Depends(get_session)) -> dict:
    """Build one post if needed and schedule it for now() — used for the boevoi pervyi pusk."""
    settings = get_settings()
    builder = PostBuilderService(settings)

    # Try to reuse an unplanned ready post of the requested type.
    candidates = Repository.list_unplanned_ready_posts(
        session, post_type=payload.post_type, limit=1
    )
    if not candidates:
        # Build one on the fly. PostBuilderService will pull from the candidate pool.
        built_ids = builder.build_posts(session, post_type=payload.post_type, limit=1)
        if not built_ids:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail=(
                    "no candidates available to build a post; "
                    "run wb-parser collect && wb-parser score first"
                ),
            )
        post = Repository.get_post(session, built_ids[0])
    else:
        post = candidates[0]

    if post is None:
        session.rollback()
        raise HTTPException(status_code=500, detail="post lookup failed after build")

    # Schedule for "now-ish" so the bridge picks it up immediately.
    now = utcnow()
    Repository.assign_planned_at(session, post.id, now)
    session.commit()
    return {
        "post_id": post.id,
        "post_type": post.post_type,
        "title": post.title,
        "planned_at": now.isoformat(),
        "with_reaction_poll": payload.with_reaction_poll,
    }


@router.post("/daily-cycle")
def daily_cycle(session: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    collector = CandidateCollectorService(settings)
    scorer = ScoringService(settings)
    builder = PostBuilderService(settings)
    planner = DailyPlannerService(settings)

    collected = collector.collect(session, source="trend", limit=120)
    scored = scorer.score_products_in_db(session, limit=500)
    built_collections = builder.build_posts(session, post_type="collection", limit=6)
    built_singles = builder.build_posts(session, post_type="single", limit=4)

    # Use sensible defaults from channel_analysis_report.txt §10.
    default_slots = [
        SlotSpec(time="10:00", type="collection"),
        SlotSpec(time="12:00", type="single"),
        SlotSpec(time="14:00", type="collection", with_reaction_poll=True),
        SlotSpec(time="16:00", type="collection"),
        SlotSpec(time="19:00", type="single"),
    ]
    plan = planner.plan_day(session, default_slots)
    session.commit()
    return {
        "collected": collected,
        "scored": scored,
        "built": {
            "collection_post_ids": built_collections,
            "single_post_ids": built_singles,
        },
        "plan": plan.to_dict(),
    }


@router.get("/status")
def admin_status(session: Session = Depends(get_session)) -> dict:
    now = utcnow()
    horizon = now + timedelta(hours=36)
    upcoming_stmt = (
        select(Post)
        .where(Post.publication_status.in_(("ready", "locked")))
        .where(Post.planned_at.is_not(None))
        .where(Post.planned_at <= horizon)
        .order_by(Post.planned_at.asc())
        .limit(50)
    )
    upcoming = [
        {
            "post_id": p.id,
            "post_type": p.post_type,
            "title": p.title,
            "planned_at": p.planned_at.isoformat() if p.planned_at else None,
            "publication_status": p.publication_status,
        }
        for p in session.scalars(upcoming_stmt)
    ]

    recent_stmt = (
        select(Post)
        .where(Post.publication_status == "published")
        .order_by(Post.published_at.desc().nulls_last())
        .limit(10)
    )
    recent = [
        {
            "post_id": p.id,
            "post_type": p.post_type,
            "title": p.title,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "telegram_url": p.telegram_url,
        }
        for p in session.scalars(recent_stmt)
    ]

    return {"now": now.isoformat(), "upcoming": upcoming, "recently_published": recent}
