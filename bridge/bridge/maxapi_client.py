"""Thin async client for the MAX gateway publication-jobs API."""

from __future__ import annotations

from typing import Any

import httpx


class MaxApiClient:
    """Wrapper around an :class:`httpx.AsyncClient` pointed at the gateway."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        base_url: str | None = None,
    ) -> None:
        self._client = client
        self._token = token
        self._base_url = (base_url or "").rstrip("/")

    def _url(self, path: str) -> str:
        if self._base_url:
            return f"{self._base_url}{path}"
        return path

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def create_publication_job(
        self,
        *,
        account_id: str,
        body: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._url(f"/v1/accounts/{account_id}/publication-jobs"),
            json=body,
            headers=self._headers(idempotency_key=idempotency_key),
        )
        response.raise_for_status()
        return response.json()

    async def get_publication_job(self, *, account_id: str, job_id: str) -> dict[str, Any]:
        response = await self._client.get(
            self._url(f"/v1/accounts/{account_id}/publication-jobs/{job_id}"),
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()
