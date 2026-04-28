from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.affiliate import AffiliateClient
from app.clients.wildberries import WildberriesClient
from app.config import Settings
from app.db.repositories import Repository
from app.services.filter_products import FilterService
from app.services.normalize_product import normalize_wb_product

logger = logging.getLogger(__name__)


class CandidateCollectorService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.filter_service = FilterService(settings)
        self.affiliate_client = AffiliateClient(settings)

    async def _collect_raw_async(
        self,
        source: str,
        limit: int,
        query: str | None = None,
        category_id: str | None = None,
        article_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        article_ids = article_ids or []
        async with WildberriesClient(self.settings) as client:
            if source == "search":
                queries = [query] if query else self.settings.load_yaml_config("search_queries.yml", {}).get(
                    "queries", []
                )
                raw: list[dict[str, Any]] = []
                if not queries:
                    return raw
                per_query = max(1, limit // max(1, len(queries)))
                for item in queries:
                    raw.extend(await client.search_products(item, per_query))
                return raw[:limit]

            if source == "trend":
                trend_keywords = self.settings.load_yaml_config("trend_keywords.yml", {}).get(
                    "trend_keywords", []
                )
                raw = []
                per_query = max(1, limit // max(1, len(trend_keywords[:10] or [1])))
                for keyword in trend_keywords[:10]:
                    raw.extend(await client.search_products(str(keyword), per_query))
                return raw[:limit]

            if source == "category":
                if not category_id:
                    categories = self.settings.load_yaml_config("categories.yml", {}).get("categories", [])
                    raw = []
                    enabled_categories = [item for item in categories if item.get("enabled")]
                    per_category = max(1, limit // max(1, len(enabled_categories)))
                    for item in enabled_categories:
                        raw.extend(await client.category_products(str(item["id"]), per_category))
                    return raw[:limit]
                return await client.category_products(category_id, limit)

            if source in {"manual", "refresh"}:
                return await client.get_many_by_article_ids(article_ids[:limit])

            raise ValueError(f"Unknown source: {source}")

    def collect(
        self,
        session: Session,
        source: str,
        limit: int = 100,
        query: str | None = None,
        category_id: str | None = None,
        article_ids: list[str] | None = None,
        premium_mode: bool = False,
    ) -> dict[str, int]:
        raw_products = asyncio.run(
            self._collect_raw_async(
                source=source,
                limit=limit,
                query=query,
                category_id=category_id,
                article_ids=article_ids,
            )
        )
        processed = 0
        accepted = 0
        rejected = 0
        for raw in raw_products:
            processed += 1
            try:
                product = normalize_wb_product(raw, source=source, source_url=query or category_id)
                if product is None:
                    rejected += 1
                    continue
                product.affiliate_url = self.affiliate_client.build_url(product.canonical_url)

                filter_result = self.filter_service.evaluate(product, premium_mode=premium_mode)
                product.risk_flags = filter_result.risk_flags

                Repository.upsert_product(session, product)
                Repository.add_product_snapshot(session, product, raw_payload=raw)
                Repository.save_media_assets(session, product.article_id, product.image_urls, product.main_image_url)

                if filter_result.accepted:
                    accepted += 1
                else:
                    rejected += 1
            except Exception as exc:
                logger.exception("Failed to process raw product: %s", exc)
                rejected += 1
        return {"processed": processed, "accepted": accepted, "rejected": rejected}

    def refresh_article(self, session: Session, article_id: str) -> dict[str, int]:
        return self.collect(
            session=session,
            source="refresh",
            limit=1,
            article_ids=[article_id],
        )
