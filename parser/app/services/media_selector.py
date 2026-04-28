from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.schemas.product import ParsedProduct
from app.services.deduplication import dedupe_strings

logger = logging.getLogger(__name__)


class MediaSelectorService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def select(self, product: ParsedProduct, min_images: int = 3, max_images: int = 10) -> list[str]:
        images = dedupe_strings(product.image_urls)
        if product.main_image_url and product.main_image_url not in images:
            images.insert(0, product.main_image_url)
        selected = [url for url in images if url.startswith("http")]
        selected = selected[:max_images]
        if len(selected) < min_images:
            return selected
        return selected

    async def cache_remote_media(self, article_id: str, urls: list[str]) -> list[dict[str, str | None]]:
        results: list[dict[str, str | None]] = []
        if not urls:
            return results
        self.settings.media_cache_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=20) as client:
            for idx, url in enumerate(urls, start=1):
                local_path: str | None = None
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    ext = ".jpg"
                    file_path = self.settings.media_cache_dir / f"{article_id}_{idx}{ext}"
                    file_path.write_bytes(response.content)
                    local_path = str(file_path)
                except Exception as exc:
                    logger.warning("Failed to cache media %s for %s: %s", url, article_id, exc)
                results.append({"url": url, "local_path": local_path})
        return results
