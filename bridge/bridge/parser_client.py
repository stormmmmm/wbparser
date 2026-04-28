"""Thin async client for the WB parser publisher API.

Only the four endpoints the bridge actually needs are wrapped:

* ``GET  /api/v1/posts/next``
* ``POST /api/v1/posts/{post_id}/lock``
* ``POST /api/v1/posts/{post_id}/published``
* ``POST /api/v1/posts/{post_id}/failed``
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class ParserClient:
    """Wrapper around an :class:`httpx.AsyncClient` pointed at the parser."""

    def __init__(self, client: httpx.AsyncClient, *, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = (base_url or "").rstrip("/")

    def _url(self, path: str) -> str:
        if self._base_url:
            return f"{self._base_url}{path}"
        return path

    async def get_next_posts(
        self, *, limit: int = 1, post_type: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if post_type is not None:
            params["post_type"] = post_type
        response = await self._client.get(self._url("/api/v1/posts/next"), params=params)
        response.raise_for_status()
        return response.json()

    async def lock_post(
        self, post_id: str, *, worker_id: str, lock_ttl_seconds: int
    ) -> dict[str, Any] | None:
        response = await self._client.post(
            self._url(f"/api/v1/posts/{post_id}/lock"),
            json={"worker_id": worker_id, "lock_ttl_seconds": lock_ttl_seconds},
        )
        if response.status_code == 409:
            return None
        response.raise_for_status()
        return response.json()

    async def mark_published(
        self,
        post_id: str,
        *,
        message_id: str,
        message_url: str | None,
        published_at: datetime,
        raw_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._url(f"/api/v1/posts/{post_id}/published"),
            json={
                "telegram_message_id": message_id,
                "telegram_url": message_url,
                "published_at": published_at.isoformat(),
                "raw_response": raw_response or {},
            },
        )
        response.raise_for_status()
        return response.json()

    async def mark_failed(
        self,
        post_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool = True,
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._url(f"/api/v1/posts/{post_id}/failed"),
            json={
                "error_code": error_code,
                "error_message": error_message,
                "retryable": retryable,
            },
        )
        response.raise_for_status()
        return response.json()
