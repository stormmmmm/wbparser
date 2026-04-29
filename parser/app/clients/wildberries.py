from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import quote

from curl_cffi import requests as curl_requests
import httpx

from app.config import Settings
from app.utils.retries import retry_async
from app.utils.urls import extract_article_id

logger = logging.getLogger(__name__)


class WildberriesClient:
    SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v14/search"
    CARD_DETAIL_URL = "https://card.wb.ru/cards/v4/detail"

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
        self._proxy_url = (
            settings.WB_PROXY_URL
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
        )
        self._proxy_pool = self._load_proxy_pool()

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
                payload = await asyncio.to_thread(self._request_json_browser_like, url, params)
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

    def _request_json_browser_like(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for proxy_url in self._proxy_candidates():
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            try:
                response = curl_requests.get(
                    url,
                    params=params,
                    headers=self.headers,
                    proxies=proxies,
                    impersonate="chrome124",
                    timeout=self.settings.WB_REQUEST_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 - rotate past bad proxy endpoints
                last_error = exc
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = httpx.HTTPStatusError(
                    f"retryable status: {response.status_code}",
                    request=httpx.Request("GET", url, params=params),
                    response=httpx.Response(response.status_code, text=response.text),
                )
                continue
            if response.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"status: {response.status_code}",
                    request=httpx.Request("GET", url, params=params),
                    response=httpx.Response(response.status_code, text=response.text),
                )
            return response.json()
        if last_error is not None:
            raise last_error
        raise ValueError("WB request did not produce a response")

    def _proxy_candidates(self) -> list[str | None]:
        if self._proxy_pool:
            attempts = min(
                max(1, self.settings.WB_PROXY_ATTEMPTS_PER_REQUEST),
                len(self._proxy_pool),
            )
            start = random.randrange(len(self._proxy_pool))
            return [
                self._proxy_pool[(start + offset) % len(self._proxy_pool)]
                for offset in range(attempts)
            ]
        if self._proxy_url:
            return [self._proxy_url]
        return [None]

    def _load_proxy_pool(self) -> list[str]:
        file_names = [
            item.strip()
            for item in self.settings.WB_PROXY_POOL_FILES.replace(",", ";").split(";")
            if item.strip()
        ]
        proxies: list[str] = []
        for file_name in file_names:
            path = Path(file_name)
            if not path.exists():
                logger.warning("WB proxy pool file does not exist: %s", path)
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                logger.warning("Failed to read WB proxy pool file %s: %s", path, exc)
                continue
            for line in lines:
                proxy = self._format_proxy_url(line.strip())
                if proxy:
                    proxies.append(proxy)
        if self._proxy_url:
            proxies.insert(0, self._proxy_url)
        return list(dict.fromkeys(proxies))

    @staticmethod
    def _format_proxy_url(line: str) -> str | None:
        if not line:
            return None
        if line.startswith(("http://", "https://")):
            return line
        try:
            host, port, user, password = line.split(":", 3)
        except ValueError:
            logger.warning("Ignoring malformed WB proxy entry")
            return None
        if host == "pool.proxys.io":
            # The pool currently presents this certificate on HTTPS CONNECT.
            host = "pool2.infatica.io"
        return (
            f"https://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@{host}:{port}"
        )

    def _cache_raw_payload(self, payload: dict[str, Any], tag: str) -> Path:
        self.settings.raw_cache_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{tag}-{int(monotonic() * 1000)}-{random.randint(1000, 9999)}.json"
        path = self.settings.raw_cache_dir / file_name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _extract_products_from_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
        top_level = payload.get("products")
        if isinstance(top_level, list) and top_level:
            return top_level
        data = payload.get("data", {})
        products = data.get("products") or data.get("items") or []
        if isinstance(products, list) and products:
            return products
        search_result = payload.get("search_result", {})
        if isinstance(search_result, dict):
            products = search_result.get("products") or []
            if isinstance(products, list):
                return products
        if isinstance(top_level, list):
            return top_level
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
