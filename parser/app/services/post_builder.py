from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Product, SelectedCandidate
from app.db.repositories import Repository
from app.services.media_selector import MediaSelectorService
from app.utils.text import short_title
from app.utils.time import utcnow


class PostBuilderService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.media_selector = MediaSelectorService(settings)

    def _single_title(self, product: Product) -> str:
        if product.category_name:
            return f"{short_title(product.title, 50)}"
        return "Находка дня с Wildberries"

    def _single_text(self, product: Product, candidate: SelectedCandidate) -> str:
        line1 = candidate.reason_for_selection or "красивая и практичная находка"
        line2 = (
            f"рейтинг {product.rating:.1f} и {product.feedbacks_count} отзывов"
            if product.rating and product.feedbacks_count
            else "похожа на удачную покупку по отзывам"
        )
        price_line = f"{product.current_price} ₽" if product.current_price else "цена уточняется"
        if product.old_price and product.discount_percent:
            line3 = f"сейчас {price_line}, было {product.old_price} ₽ (-{product.discount_percent}%)"
        else:
            line3 = f"цена: {price_line}"
        return (
            f"{self._single_title(product)}\n\n"
            f"❤️ {line1}\n"
            f"❤️ {line2}\n"
            f"❤️ {line3}\n\n"
            f"{product.article_id} - {price_line}\n"
            "(клик по артикулу, чтобы перейти на товар)"
        )

    def _collection_title(self, products: list[Product]) -> str:
        category = next((p.category_name for p in products if p.category_name), None)
        if category:
            return f"Подборка: {category}"
        return "Подборка находок с Wildberries"

    def _collection_text(self, rows: list[tuple[SelectedCandidate, Product]]) -> str:
        title = self._collection_title([row[1] for row in rows])
        lines = [title, ""]
        for idx, (candidate, product) in enumerate(rows, start=1):
            price = f"{product.current_price} ₽" if product.current_price else "цена уточняется"
            reason = candidate.reason_for_selection or short_title(product.title, 42)
            lines.append(f"{idx}. {product.article_id} - {price} / {reason}")
        lines.append("")
        lines.append("(клик по артикулу, чтобы перейти на товар)")
        return "\n".join(lines)

    def _build_media(self, rows: list[tuple[SelectedCandidate, Product]]) -> list[dict]:
        media: list[dict] = []
        position = 1
        for _, product in rows:
            selected = self.media_selector.select(
                product=product_to_schema(product),
                min_images=1,
                max_images=2,
            )
            for url in selected:
                media.append(
                    {
                        "position": position,
                        "article_id": product.article_id,
                        "url": url,
                        "type": "photo",
                        "caption": None,
                        "local_path": None,
                    }
                )
                position += 1
                if position > 10:
                    break
            if position > 10:
                break
        return media

    def _build_items(self, rows: list[tuple[SelectedCandidate, Product]]) -> list[dict]:
        items: list[dict] = []
        for idx, (candidate, product) in enumerate(rows, start=1):
            displayed_url = product.affiliate_url or product.canonical_url
            items.append(
                {
                    "position": idx,
                    "article_id": product.article_id,
                    "title": product.title,
                    "price": product.current_price,
                    "old_price": product.old_price,
                    "discount_percent": product.discount_percent,
                    "displayed_url": displayed_url,
                    "caption": candidate.reason_for_selection,
                }
            )
        return items

    def build_posts(self, session: Session, post_type: str = "collection", limit: int = 10) -> list[str]:
        rows = Repository.list_candidates_for_post_build(session, limit=max(limit * 10, 50), min_score=70)
        if not rows:
            return []

        cooldown_days = self.settings.DEFAULT_POST_COOLDOWN_DAYS
        eligible_rows: list[tuple[SelectedCandidate, Product]] = []
        for candidate, product in rows:
            if Repository.is_article_on_cooldown(session, product.article_id, cooldown_days):
                continue
            eligible_rows.append((candidate, product))

        created_post_ids: list[str] = []
        if post_type == "single":
            for candidate, product in eligible_rows[:limit]:
                post_id = Repository.generate_post_id("single", [product.article_id], bucket="single")
                media = self._build_media([(candidate, product)])
                items = self._build_items([(candidate, product)])
                fresh_until = utcnow() + timedelta(minutes=self.settings.POST_REVALIDATE_MINUTES)
                post = Repository.upsert_post(
                    session=session,
                    post_id=post_id,
                    post_type="single",
                    title=self._single_title(product),
                    text=self._single_text(product, candidate),
                    media_urls=media,
                    parse_mode=None,
                    fresh_until=fresh_until,
                    planned_at=None,
                    publication_status="ready",
                )
                Repository.replace_post_items(session, post.id, items)
                Repository.mark_candidate_status(session, candidate.id, "ready")
                created_post_ids.append(post.id)
            return created_post_ids

        grouped: dict[str, list[tuple[SelectedCandidate, Product]]] = defaultdict(list)
        for candidate, product in eligible_rows:
            key = product.category_name or "misc"
            grouped[key].append((candidate, product))

        for _, group_rows in grouped.items():
            if len(created_post_ids) >= limit:
                break
            if len(group_rows) < 4:
                continue
            selected_rows = group_rows[:8]
            article_ids = [item[1].article_id for item in selected_rows]
            post_id = Repository.generate_post_id("collection", article_ids, bucket="collection")
            media = self._build_media(selected_rows)
            items = self._build_items(selected_rows)
            fresh_until = utcnow() + timedelta(minutes=self.settings.POST_REVALIDATE_MINUTES)
            post = Repository.upsert_post(
                session=session,
                post_id=post_id,
                post_type="collection",
                title=self._collection_title([row[1] for row in selected_rows]),
                text=self._collection_text(selected_rows),
                media_urls=media,
                parse_mode=None,
                fresh_until=fresh_until,
                planned_at=None,
                publication_status="ready",
            )
            Repository.replace_post_items(session, post.id, items)
            for candidate, _ in selected_rows:
                Repository.mark_candidate_status(session, candidate.id, "ready")
            created_post_ids.append(post.id)
        return created_post_ids


def product_to_schema(product: Product):
    from app.schemas.product import ParsedProduct

    return ParsedProduct(
        article_id=product.article_id,
        title=product.title,
        normalized_title=product.normalized_title,
        brand=product.brand,
        category_id=product.category_id,
        category_name=product.category_name,
        subject_name=product.subject_name,
        tags=product.tags or [],
        price=product.current_price,
        old_price=product.old_price,
        discount_percent=product.discount_percent,
        rating=product.rating,
        feedbacks_count=product.feedbacks_count,
        orders_count=product.orders_count,
        popularity_score=product.popularity_score,
        main_image_url=product.main_image_url,
        image_urls=product.image_urls or [],
        product_url=product.product_url,
        canonical_url=product.canonical_url,
        affiliate_url=product.affiliate_url,
        availability=product.availability,
        sizes_available=product.sizes or [],
        colors_available=product.colors or [],
        seller_id=product.seller_id,
        seller_name=product.seller_name,
        source=product.source,
        source_url=None,
        detected_at=product.detected_at,
        first_seen_at=product.first_seen_at,
        last_checked_at=product.last_checked_at,
    )
