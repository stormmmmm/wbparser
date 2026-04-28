"""Thin REST client for the maxapi gateway used by wbpost CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class MaxApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class StartLoginResult:
    challenge_id: str
    expires_at: str | None
    requires_two_factor: bool = False


@dataclass
class VerifyLoginResult:
    account_id: str
    phone: str | None


@dataclass
class FoundChannel:
    id: str
    title: str
    can_publish: bool


class MaxApiClient:
    def __init__(self, base_url: str, *, api_key: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MaxApiClient":
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
        raise MaxApiError(
            f"maxapi returned {resp.status_code} for {resp.request.method} {resp.request.url.path}",
            status_code=resp.status_code,
            body=body,
        )

    def health(self) -> dict[str, Any]:
        resp = self._client.get("/health")
        self._raise_for(resp)
        return resp.json()

    def start_login(self, *, phone: str, device_name: str | None = None) -> StartLoginResult:
        body: dict[str, Any] = {"phone": phone}
        if device_name:
            body["device_name"] = device_name
        resp = self._client.post("/v1/accounts/login/start", json=body)
        self._raise_for(resp)
        data = resp.json()
        return StartLoginResult(
            challenge_id=data["challenge_id"],
            expires_at=data.get("expires_at"),
            requires_two_factor=bool(data.get("requires_two_factor", False)),
        )

    def verify_login(
        self,
        *,
        challenge_id: str,
        code: str,
        two_factor_password: str | None = None,
    ) -> VerifyLoginResult:
        body: dict[str, Any] = {"challenge_id": challenge_id, "code": code}
        if two_factor_password:
            body["two_factor_password"] = two_factor_password
        resp = self._client.post("/v1/accounts/login/verify", json=body)
        self._raise_for(resp)
        data = resp.json()
        account = data.get("account", data)
        # Real maxapi uses `account_id` + `phone_masked`; memory backend may emit `id`.
        account_id = account.get("account_id") or account.get("id")
        phone = account.get("phone") or account.get("phone_masked")
        return VerifyLoginResult(account_id=account_id, phone=phone)

    def list_accounts(self) -> list[dict[str, Any]]:
        resp = self._client.get("/v1/accounts")
        self._raise_for(resp)
        return resp.json().get("items", [])

    def find_channel(
        self,
        *,
        account_id: str,
        title: str,
        title_match: str = "exact",
        case_insensitive: bool = True,
        only_writable: bool = False,
    ) -> FoundChannel:
        resp = self._client.get(
            f"/v1/accounts/{account_id}/channels/find",
            params={
                "title": title,
                "title_match": title_match,
                "case_insensitive": str(case_insensitive).lower(),
                "only_writable": str(only_writable).lower(),
            },
        )
        if resp.status_code == 404:
            # Fall back to substring search before giving up.
            self._raise_for(resp)
        self._raise_for(resp)
        data = resp.json()
        permissions = data.get("permissions") or {}
        channel_id = data.get("channel_id") or data.get("id")
        return FoundChannel(
            id=channel_id,
            title=data.get("title", title),
            can_publish=bool(permissions.get("can_publish", True)),
        )
