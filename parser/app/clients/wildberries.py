from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from app.config import Settings
from app.utils.retries import retry_async
from app.utils.urls import extract_article_id

logger = logging.getLogger(__name__)


class WildberriesClient:
    SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v13/search"
    CARD_DETAIL_URL = "https://card.wb.ru/cards/v2/detail"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout = httpx.Timeout(settings.WB_REQUEST_TIMEOUT_SECONDS)
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
        self._client = httpx.AsyncClient(timeout=self.timeout, headers=self.headers)
        self._semaphore = asyncio.Semaphore(max(1, settings.WB_MAX_CONCURRENCY))
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "WildberriesClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _respect_rate_limit(self) -> None:
        interval = 1.0 / max(self.settings.WB_RATE_LIMIT_PER_SECOND, 0.1)
        async with self._rate_lock:
            now = monotonic()
            elapsed = now - self._last_request_at
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
            self._last_request_at = monotonic()

    async def _request_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        cache_tag: str | None = None,
    ) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            async with self._semaphore:
                await self._respect_rate_limit()
                response = await self._client.get(url, params=params)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable status: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                self._cache_raw_payload(payload, cache_tag or "wb")
                return payload

        try:
            return await retry_async(
                _call,
                retries=4,
                base_delay=0.7,
                retry_exceptions=(httpx.TransportError, httpx.HTTPStatusError, ValueError),
            )
        except Exception as exc:
            logger.error("WB request failed url=%s params=%s error=%s", url, params, exc)
            raise

    def _cache_raw_payload(self, payload: dict[str, Any], tag: str) -> Path:
        self.settings.raw_cache_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{tag}-{int(monotonic() * 1000)}-{random.randint(1000, 9999)}.json"
        path = self.settings.raw_cache_dir / file_name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _extract_products_from_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", {})
        products = data.get("products") or data.get("items") or []
        if isinstance(products, list):
            return products
        return []

    async def search_products(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        all_products: list[dict[str, Any]] = []
        page = 1
        while len(all_products) < limit and page <= 10:
            params = {
                "ab_testing": "false",
                "appType": 1,
                "curr": "rub",
                "dest": -1257786,
                "query": query,
                "resultset": "catalog",
                "sort": "popular",
                "page": page,
            }
            payload = await self._request_json(self.SEARCH_URL, params=params, cache_tag="search")
            products = self._extract_products_from_search(payload)
            if not products:
                break
            all_products.extend(products)
            page += 1
        return all_products[:limit]

    async def category_products(self, category_id: str, limit: int = 100) -> list[dict[str, Any]]:
        # В WB category API меняется часто; fallback через query=category_id.
        return await self.search_products(query=category_id, limit=limit)

    async def get_product_detail(self, article_id: str) -> dict[str, Any] | None:
        params = {
            "appType": 1,
            "curr": "rub",
            "dest": -1257786,
            "nm": article_id,
        }
        payload = await self._request_json(self.CARD_DETAIL_URL, params=params, cache_tag="detail")
        products = self._extract_products_from_search(payload)
        if products:
            return products[0]
        # Иногда структура приходит в data.products без общего вида search.
        data = payload.get("data", {})
        fallback = data.get("products")
        if isinstance(fallback, list) and fallback:
            return fallback[0]
        return None

    async def get_many_by_article_ids(self, article_ids: list[str]) -> list[dict[str, Any]]:
        tasks = [self.get_product_detail(article_id) for article_id in article_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        products: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Failed to fetch product detail: %s", result)
                continue
            if result:
                products.append(result)
        return products

    @staticmethod
    def normalize_article_id(value: str | int | None) -> str | None:
        return extract_article_id(value)
