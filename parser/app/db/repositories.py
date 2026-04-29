from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    ClickMetric,
    ExcludedArticle,
    MediaAsset,
    Post,
    PostItem,
    Product,
    ProductSnapshot,
    PublicationMetric,
    SelectedCandidate,
)
from app.schemas.post import (
    ReadyPost,
    ReadyPostButton,
    ReadyPostItem,
    ReadyPostMedia,
    ReactionsHint,
)
from app.schemas.product import ParsedProduct, ProductScoreResult
from app.utils.text import short_title
from app.utils.time import utcnow


class Repository:
    @staticmethod
    def upsert_product(session: Session, product: ParsedProduct) -> Product:
        db_product = session.scalar(select(Product).where(Product.article_id == product.article_id))
        if db_product is None:
            db_product = Product(article_id=product.article_id, first_seen_at=product.first_seen_at)
            session.add(db_product)

        db_product.title = product.title
        db_product.normalized_title = product.normalized_title
        db_product.brand = product.brand
        db_product.category_id = product.category_id
        db_product.category_name = product.category_name
        db_product.subject_name = product.subject_name
        db_product.tags = product.tags
        db_product.product_url = product.product_url
        db_product.canonical_url = product.canonical_url
        db_product.affiliate_url = product.affiliate_url
        db_product.main_image_url = product.main_image_url
        db_product.image_urls = product.image_urls
        db_product.current_price = product.price
        db_product.old_price = product.old_price
        db_product.discount_percent = product.discount_percent
        db_product.rating = product.rating
        db_product.feedbacks_count = product.feedbacks_count
        db_product.orders_count = product.orders_count
        db_product.popularity_score = product.popularity_score
        db_product.availability = product.availability
        db_product.sizes = product.sizes_available
        db_product.colors = product.colors_available
        db_product.seller_id = product.seller_id
        db_product.seller_name = product.seller_name
        db_product.detected_at = product.detected_at
        db_product.last_checked_at = product.last_checked_at
        db_product.source = product.source
        db_product.updated_at = utcnow()
        session.flush()
        return db_product

    @staticmethod
    def add_product_snapshot(
        session: Session, product: ParsedProduct, raw_payload: dict[str, Any]
    ) -> ProductSnapshot:
        snapshot = ProductSnapshot(
            article_id=product.article_id,
            checked_at=product.last_checked_at,
            price=product.price,
            old_price=product.old_price,
            discount_percent=product.discount_percent,
            rating=product.rating,
            feedbacks_count=product.feedbacks_count,
            availability=product.availability,
            raw_payload=raw_payload,
        )
        session.add(snapshot)
        session.flush()
        return snapshot

    @staticmethod
    def save_media_assets(
        session: Session, article_id: str, media_urls: list[str], main_image: str | None
    ) -> None:
        session.execute(delete(MediaAsset).where(MediaAsset.article_id == article_id))
        for position, url in enumerate(media_urls, start=1):
            asset = MediaAsset(
                article_id=article_id,
                source_url=url,
                local_path=None,
                media_type="photo",
                is_main=(url == main_image) or (position == 1 and main_image is None),
                visual_rank=position,
            )
            session.add(asset)
        session.flush()

    @staticmethod
    def get_product(session: Session, article_id: str) -> Product | None:
        return session.scalar(select(Product).where(Product.article_id == article_id))

    @staticmethod
    def list_products_for_scoring(session: Session, limit: int = 500) -> list[Product]:
        stmt = (
            select(Product)
            .where(Product.availability.is_(True))
            .order_by(Product.last_checked_at.desc())
            .limit(limit)
        )
        return list(session.scalars(stmt))

    @staticmethod
    def save_candidate_score(
        session: Session,
        article_id: str,
        score: ProductScoreResult,
        risk_flags: list[str],
        reviewer_note: str | None = None,
    ) -> SelectedCandidate:
        candidate = SelectedCandidate(
            article_id=article_id,
            selected_at=utcnow(),
            selection_score=score.selection_score,
            visual_score=score.visual_score,
            audience_fit_score=score.audience_fit_score,
            price_score=score.price_score,
            discount_score=score.discount_score,
            rating_score=score.rating_score,
            feedback_score=score.feedback_score,
            trend_score=score.trend_score,
            season_score=score.season_score,
            risk_penalty=score.risk_penalty,
            reason_for_selection=score.reason_for_selection,
            status=score.status,
            reviewer_note=reviewer_note,
            risk_flags=risk_flags,
        )
        session.add(candidate)
        session.flush()
        return candidate

    @staticmethod
    def list_candidates_for_post_build(
        session: Session,
        limit: int = 50,
        min_score: float = 55.0,
        statuses: tuple[str, ...] = ("ready", "approved", "needs_review", "new"),
    ) -> list[tuple[SelectedCandidate, Product]]:
        stmt = (
            select(SelectedCandidate, Product)
            .join(Product, Product.article_id == SelectedCandidate.article_id)
            .where(
                and_(
                    SelectedCandidate.selection_score >= min_score,
                    SelectedCandidate.status.in_(statuses),
                    Product.availability.is_(True),
                )
            )
            .order_by(SelectedCandidate.selection_score.desc(), SelectedCandidate.selected_at.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).all())

    @staticmethod
    def mark_candidate_status(session: Session, candidate_id: int, status: str) -> None:
        candidate = session.scalar(select(SelectedCandidate).where(SelectedCandidate.id == candidate_id))
        if not candidate:
            return
        candidate.status = status
        session.flush()

    @staticmethod
    def generate_post_id(post_type: str, article_ids: list[str], bucket: str = "default") -> str:
        payload = {"post_type": post_type, "article_ids": article_ids, "bucket": bucket}
        digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return f"{post_type}-{digest}"

    @staticmethod
    def upsert_post(
        session: Session,
        post_id: str,
        post_type: str,
        title: str,
        text: str,
        media_urls: list[dict[str, Any]],
        parse_mode: str | None,
        fresh_until,
        planned_at,
        publication_status: str = "ready",
    ) -> Post:
        post = session.scalar(select(Post).where(Post.id == post_id))
        if post is None:
            post = Post(id=post_id)
            session.add(post)

        post.post_type = post_type
        post.title = title
        post.post_text = text
        post.media_urls = media_urls
        post.parse_mode = parse_mode
        post.fresh_until = fresh_until
        post.planned_at = planned_at
        post.publication_status = publication_status
        if publication_status != "published":
            post.telegram_message_id = None
            post.telegram_url = None
            post.published_at = None
        post.updated_at = utcnow()
        session.flush()
        return post

    @staticmethod
    def replace_post_items(
        session: Session,
        post_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        session.execute(delete(PostItem).where(PostItem.post_id == post_id))
        seen_articles: set[str] = set()
        for item in items:
            article_id = str(item["article_id"])
            if article_id in seen_articles:
                continue
            seen_articles.add(article_id)
            session.add(
                PostItem(
                    post_id=post_id,
                    article_id=article_id,
                    position=item["position"],
                    displayed_price=item.get("price"),
                    displayed_old_price=item.get("old_price"),
                    displayed_discount=item.get("discount_percent"),
                    displayed_url=item["displayed_url"],
                    displayed_caption=item.get("caption"),
                )
            )
        session.flush()

    @staticmethod
    def is_article_on_cooldown(session: Session, article_id: str, cooldown_days: int) -> bool:
        cutoff = utcnow() - timedelta(days=cooldown_days)
        stmt = (
            select(PostItem.id)
            .join(Post, Post.id == PostItem.post_id)
            .where(
                and_(
                    PostItem.article_id == article_id,
                    Post.created_at >= cutoff,
                    Post.publication_status.in_(("ready", "locked", "published")),
                )
            )
            .limit(1)
        )
        return session.scalar(stmt) is not None

    @staticmethod
    def get_next_ready_posts(
        session: Session, limit: int = 1, post_type: str | None = None
    ) -> list[Post]:
        now = utcnow()
        stmt = select(Post).where(
            and_(
                Post.publication_status == "ready",
                Post.fresh_until >= now,
                or_(Post.planned_at.is_(None), Post.planned_at <= now),
            )
        )
        if post_type:
            stmt = stmt.where(Post.post_type == post_type)
        # Posts with an explicit planned_at run earliest first; unplanned posts
        # fall back to creation order.
        stmt = stmt.order_by(Post.planned_at.asc().nulls_last(), Post.created_at.asc()).limit(limit)
        return list(session.scalars(stmt))

    @staticmethod
    def list_unplanned_ready_posts(
        session: Session,
        post_type: str | None = None,
        limit: int = 50,
    ) -> list[Post]:
        """Posts ready to be assigned a slot (status=ready, planned_at IS NULL)."""
        now = utcnow()
        stmt = select(Post).where(
            and_(
                Post.publication_status == "ready",
                Post.fresh_until >= now,
                Post.planned_at.is_(None),
            )
        )
        if post_type:
            stmt = stmt.where(Post.post_type == post_type)
        stmt = stmt.order_by(Post.created_at.asc()).limit(limit)
        return list(session.scalars(stmt))

    @staticmethod
    def assign_planned_at(session: Session, post_id: str, planned_at) -> Post | None:
        post = Repository.get_post(session, post_id)
        if post is None:
            return None
        post.planned_at = planned_at
        post.updated_at = utcnow()
        session.flush()
        return post

    @staticmethod
    def get_post(session: Session, post_id: str | int) -> Post | None:
        return session.scalar(select(Post).where(Post.id == str(post_id)))

    @staticmethod
    def lock_post(session: Session, post_id: str | int, worker_id: str, ttl_seconds: int) -> Post | None:
        post = Repository.get_post(session, post_id)
        if post is None:
            return None
        now = utcnow()
        if post.publication_status in {"published", "expired"}:
            return None
        if post.publication_status == "locked" and post.lock_until and post.lock_until > now:
            return None
        if post.publication_status not in {"ready", "failed", "locked"}:
            return None

        post.publication_status = "locked"
        post.lock_worker_id = worker_id
        post.lock_until = now + timedelta(seconds=ttl_seconds)
        post.updated_at = now
        session.flush()
        return post

    @staticmethod
    def mark_post_published(
        session: Session,
        post_id: str | int,
        telegram_message_id: str,
        telegram_url: str | None,
        published_at,
    ) -> Post | None:
        post = Repository.get_post(session, post_id)
        if post is None:
            return None
        post.publication_status = "published"
        post.telegram_message_id = telegram_message_id
        post.telegram_url = telegram_url
        post.published_at = published_at
        post.lock_until = None
        post.lock_worker_id = None
        post.updated_at = utcnow()
        session.flush()
        return post

    @staticmethod
    def mark_post_failed(
        session: Session,
        post_id: str | int,
        retryable: bool,
        error_message: str | None = None,
        retry_after_seconds: int = 300,
    ) -> Post | None:
        post = Repository.get_post(session, post_id)
        if post is None:
            return None
        now = utcnow()
        post.publication_status = "ready" if retryable else "failed"
        post.lock_until = None
        post.lock_worker_id = None
        post.telegram_message_id = None
        post.telegram_url = None
        post.published_at = None
        if retryable:
            post.planned_at = now + timedelta(seconds=max(30, retry_after_seconds))
        if error_message:
            post.post_text = f"{post.post_text}\n\n[publication_error]: {error_message}"
        post.updated_at = now
        session.flush()
        return post

    @staticmethod
    def add_publication_metrics(
        session: Session,
        post_id: str | int,
        collected_at,
        views_count: int,
        reactions_total: int,
        reaction_breakdown: dict[str, int],
        comments_count: int,
        forwards_count: int,
    ) -> PublicationMetric | None:
        post = Repository.get_post(session, post_id)
        if post is None:
            return None
        metric = PublicationMetric(
            post_id=post.id,
            collected_at=collected_at,
            views_count=views_count,
            reactions_total=reactions_total,
            reaction_breakdown=reaction_breakdown,
            comments_count=comments_count,
            forwards_count=forwards_count,
        )
        session.add(metric)
        post.views_count = max(post.views_count, views_count)
        post.reactions_total = max(post.reactions_total, reactions_total)
        post.reaction_breakdown = reaction_breakdown
        post.updated_at = utcnow()
        session.flush()
        return metric

    @staticmethod
    def add_click_metric(
        session: Session,
        post_id: str | int,
        article_id: str,
        clicked_at,
        click_source: str | None,
        redirect_id: str | None,
        raw_payload: dict[str, Any],
    ) -> ClickMetric | None:
        post = Repository.get_post(session, post_id)
        if post is None:
            return None
        metric = ClickMetric(
            post_id=post.id,
            article_id=article_id,
            clicked_at=clicked_at,
            click_source=click_source,
            redirect_id=redirect_id,
            raw_payload=raw_payload,
        )
        session.add(metric)
        post.clicks_count += 1
        post.updated_at = utcnow()
        session.flush()
        return metric

    @staticmethod
    def list_excluded_articles(session: Session) -> set[str]:
        return {row[0] for row in session.execute(select(ExcludedArticle.article_id)).all()}

    @staticmethod
    def create_excluded_article(
        session: Session,
        article_id: str,
        reason: str,
    ) -> ExcludedArticle:
        existing = session.scalar(select(ExcludedArticle).where(ExcludedArticle.article_id == article_id))
        if existing:
            existing.reason = reason
            session.flush()
            return existing
        item = ExcludedArticle(article_id=article_id, reason=reason)
        session.add(item)
        session.flush()
        return item

    @staticmethod
    def to_ready_post(session: Session, post: Post) -> ReadyPost:
        settings = get_settings()
        item_stmt = (
            select(PostItem, Product)
            .join(Product, Product.article_id == PostItem.article_id)
            .where(PostItem.post_id == post.id)
            .order_by(PostItem.position.asc())
        )
        rows = session.execute(item_stmt).all()

        items: list[ReadyPostItem] = []
        buttons: list[ReadyPostButton] = []
        for post_item, product in rows:
            displayed_url = product.affiliate_url or product.canonical_url
            item = ReadyPostItem(
                position=post_item.position,
                article_id=product.article_id,
                title=product.title,
                short_title=short_title(product.title, 40),
                price=post_item.displayed_price,
                old_price=post_item.displayed_old_price,
                discount_percent=post_item.displayed_discount,
                rating=product.rating,
                feedbacks_count=product.feedbacks_count,
                displayed_url=displayed_url,
                canonical_url=product.canonical_url,
                affiliate_url=product.affiliate_url,
                main_image_url=product.main_image_url,
                reason_for_selection=post_item.displayed_caption,
                risk_flags=[],
            )
            items.append(item)
            buttons.append(ReadyPostButton(text=f"Открыть товар {item.position}", url=displayed_url))

        media: list[ReadyPostMedia] = []
        for media_item in post.media_urls:
            media.append(
                ReadyPostMedia(
                    position=media_item.get("position", 1),
                    article_id=media_item.get("article_id"),
                    url=media_item["url"],
                    local_path=media_item.get("local_path"),
                    type=media_item.get("type", "photo"),
                    caption=media_item.get("caption"),
                )
            )

        return ReadyPost(
            post_id=post.id,
            post_type=post.post_type,
            title=post.title,
            text=post.post_text,
            parse_mode=post.parse_mode,
            media=media,
            items=items,
            buttons=buttons,
            reactions_hint=ReactionsHint(enabled=True, text="Да - ❤️     Нет - 🔥"),
            planned_at=post.planned_at,
            fresh_until=post.fresh_until,
            publication_status=post.publication_status,
            created_at=post.created_at,
            source="wb_parser",
            version=settings.VERSION,
        )


def write_ready_posts_jsonl(path: Path, posts: list[ReadyPost]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for post in posts:
            fh.write(post.model_dump_json(ensure_ascii=False))
            fh.write("\n")
    return len(posts)
