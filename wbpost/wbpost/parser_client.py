"""Thin REST client for the WB parser used by wbpost CLI."""

from __future__ import annotations

from typing import Any

import httpx


class ParserError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ParserClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ParserClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _raise_for(self, resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise ParserError(
            f"parser returned {resp.status_code} for {resp.request.method} {resp.request.url.path}",
            status_code=resp.status_code,
            body=body,
        )

    def health(self) -> dict[str, Any]:
        # WB parser exposes /api/v1/health and /health (legacy alias).
        resp = self._client.get("/health")
        self._raise_for(resp)
        return resp.json()

    def plan_day(
        self,
        *,
        date: str | None = None,
        slots: list[dict[str, Any]] | None = None,
        timezone: str | None = None,
        posting_minute_spread: tuple[int, int] | None = None,
        minute_spread: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if date is not None:
            body["date"] = date
        if slots is not None:
            body["slots"] = slots
        if timezone is not None:
            body["timezone"] = timezone
        effective_spread = (
            posting_minute_spread if posting_minute_spread is not None else minute_spread
        )
        if effective_spread is not None:
            body["posting_minute_spread"] = list(effective_spread)
        resp = self._client.post("/api/v1/admin/plan-day", json=body)
        self._raise_for(resp)
        return resp.json()

    def post_once(
        self,
        *,
        post_type: str | None = None,
        with_reaction_poll: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"with_reaction_poll": with_reaction_poll}
        if post_type:
            body["post_type"] = post_type
        resp = self._client.post("/api/v1/admin/post-once", json=body)
        self._raise_for(resp)
        return resp.json()

    def daily_cycle(
        self,
        *,
        slots: list[dict[str, Any]] | None = None,
        timezone: str | None = None,
        posting_minute_spread: tuple[int, int] | None = None,
        minute_spread: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if slots is not None:
            body["slots"] = slots
        if timezone is not None:
            body["timezone"] = timezone
        effective_spread = (
            posting_minute_spread if posting_minute_spread is not None else minute_spread
        )
        if effective_spread is not None:
            body["posting_minute_spread"] = list(effective_spread)
        resp = self._client.post("/api/v1/admin/daily-cycle", json=body)
        self._raise_for(resp)
        return resp.json()

    def status(self) -> dict[str, Any]:
        resp = self._client.get("/api/v1/admin/status")
        self._raise_for(resp)
        return resp.json()
