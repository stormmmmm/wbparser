from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Post, PostItem, Product
from app.db.repositories import Repository
from app.db.session import get_session
from app.schemas.metrics import PostFailedIn, PostLockIn, PostMetricsIn, PostPublishedIn
from app.schemas.post import ReadyPost
from app.utils.time import utcnow

router = APIRouter(prefix="/api/v1/posts", tags=["posts"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_post_fresh(session: Session, post: Post) -> bool:
    settings = get_settings()
    now = _as_utc(utcnow())
    if _as_utc(post.fresh_until) < now:
        return False
    freshness_cutoff = now - timedelta(minutes=settings.POST_REVALIDATE_MINUTES)
    rows = session.execute(
        select(PostItem, Product)
        .join(Product, Product.article_id == PostItem.article_id)
        .where(PostItem.post_id == post.id)
    ).all()
    for _, product in rows:
        if not product.availability:
            return False
        if post.planned_at is None and _as_utc(product.last_checked_at) < freshness_cutoff:
            return False
    return True


@router.get("/next")
def get_next_posts(
    limit: int = 1,
    post_type: str | None = None,
    include_unplanned: bool = True,
    session: Session = Depends(get_session),
) -> list[ReadyPost]:
    ready_posts = Repository.get_next_ready_posts(session, limit=max(1, limit), post_type=post_type)
    if not include_unplanned:
        ready_posts = [post for post in ready_posts if post.planned_at is not None]
    result: list[ReadyPost] = []
    for post in ready_posts:
        if not _is_post_fresh(session, post):
            post.publication_status = "expired"
            post.updated_at = utcnow()
            continue
        result.append(Repository.to_ready_post(session, post))
    session.commit()
    return result


@router.post("/{post_id}/lock")
def lock_post(
    post_id: str,
    payload: PostLockIn,
    session: Session = Depends(get_session),
) -> ReadyPost:
    post = Repository.lock_post(
        session=session,
        post_id=post_id,
        worker_id=payload.worker_id,
        ttl_seconds=payload.lock_ttl_seconds,
    )
    if post is None:
        session.rollback()
        raise HTTPException(status_code=409, detail="post_unavailable_for_lock")
    session.commit()
    return Repository.to_ready_post(session, post)


@router.post("/{post_id}/published")
def mark_published(
    post_id: str,
    payload: PostPublishedIn,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    post = Repository.mark_post_published(
        session=session,
        post_id=post_id,
        telegram_message_id=payload.telegram_message_id,
        telegram_url=payload.telegram_url,
        published_at=payload.published_at,
    )
    if post is None:
        session.rollback()
        raise HTTPException(status_code=404, detail="post_not_found")
    session.commit()
    return {"status": "ok", "post_id": str(post.id), "publication_status": post.publication_status}


@router.post("/{post_id}/failed")
def mark_failed(
    post_id: str,
    payload: PostFailedIn,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    post = Repository.mark_post_failed(
        session=session,
        post_id=post_id,
        retryable=payload.retryable,
        error_message=f"{payload.error_code}: {payload.error_message}",
        retry_after_seconds=payload.retry_after_seconds,
    )
    if post is None:
        session.rollback()
        raise HTTPException(status_code=404, detail="post_not_found")
    session.commit()
    return {"status": "ok", "post_id": str(post.id), "publication_status": post.publication_status}


@router.post("/{post_id}/metrics")
def add_post_metrics(
    post_id: str,
    payload: PostMetricsIn,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    metric = Repository.add_publication_metrics(
        session=session,
        post_id=post_id,
        collected_at=payload.collected_at,
        views_count=payload.views_count,
        reactions_total=payload.reactions_total,
        reaction_breakdown=payload.reaction_breakdown,
        comments_count=payload.comments_count,
        forwards_count=payload.forwards_count,
    )
    if metric is None:
        session.rollback()
        raise HTTPException(status_code=404, detail="post_not_found")
    session.commit()
    return {"status": "ok", "post_id": str(post_id)}
