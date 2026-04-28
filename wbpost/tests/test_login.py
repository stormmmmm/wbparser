"""Tests for the maxapi-aware login flow used by ``wbpost login``."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from wbpost.config import AdminConfig, DeploymentConfig, MaxConfig
from wbpost.login import login_and_resolve_channel
from wbpost.state import load_state

BASE = "http://maxapi.test"


def _cfg(tmp_path: Path) -> AdminConfig:
    return AdminConfig(
        max=MaxConfig(phone="+79991234567", channel_name="Поищи на WB", device_name="dev"),
        deployment=DeploymentConfig(
            parser_url="http://parser.test",
            maxapi_url=BASE,
            state_path=str(tmp_path / "state.json"),
        ),
    )


def _no_existing_account(route: respx.Route) -> None:
    route.mock(return_value=httpx.Response(200, json={"items": []}))


@respx.mock
def test_login_full_flow_writes_state(tmp_path):
    cfg = _cfg(tmp_path)
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    _no_existing_account(respx.get(f"{BASE}/v1/accounts"))
    respx.post(f"{BASE}/v1/accounts/login/start").mock(
        return_value=httpx.Response(202, json={"challenge_id": "chg_123"})
    )
    respx.post(f"{BASE}/v1/accounts/login/verify").mock(
        return_value=httpx.Response(
            200,
            json={"account": {"id": "acc_NEW", "phone": "+79991234567"}},
        )
    )
    respx.get(f"{BASE}/v1/accounts/acc_NEW/channels/find").mock(
        return_value=httpx.Response(
            200,
            json={"id": "-100888", "title": "Поищи на WB", "permissions": {"can_publish": True}},
        )
    )

    outcome = login_and_resolve_channel(
        cfg, tmp_path / "state.json", prompter=lambda _msg: "654321"
    )
    assert outcome.state.account_id == "acc_NEW"
    assert outcome.state.channel_id == "-100888"
    assert outcome.reused_existing_account is False

    persisted = load_state(tmp_path / "state.json")
    assert persisted is not None
    assert persisted.account_id == "acc_NEW"


@respx.mock
def test_login_reuses_existing_account_without_sms(tmp_path):
    cfg = _cfg(tmp_path)
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx.get(f"{BASE}/v1/accounts").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "acc_KNOWN", "phone": "+79991234567"}]},
        )
    )
    start_route = respx.post(f"{BASE}/v1/accounts/login/start")
    respx.get(f"{BASE}/v1/accounts/acc_KNOWN/channels/find").mock(
        return_value=httpx.Response(
            200,
            json={"id": "-100777", "title": "Поищи на WB", "permissions": {"can_publish": True}},
        )
    )

    outcome = login_and_resolve_channel(
        cfg,
        tmp_path / "state.json",
        prompter=lambda _msg: "ignored",
    )
    assert outcome.state.account_id == "acc_KNOWN"
    assert outcome.reused_existing_account is True
    assert start_route.called is False


@respx.mock
def test_login_force_starts_sms_even_when_account_exists(tmp_path):
    cfg = _cfg(tmp_path)
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx.get(f"{BASE}/v1/accounts").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "acc_KNOWN", "phone": "+79991234567"}]},
        )
    )
    respx.post(f"{BASE}/v1/accounts/login/start").mock(
        return_value=httpx.Response(202, json={"challenge_id": "chg_force"})
    )
    respx.post(f"{BASE}/v1/accounts/login/verify").mock(
        return_value=httpx.Response(
            200,
            json={"account": {"id": "acc_FORCED", "phone": "+79991234567"}},
        )
    )
    respx.get(f"{BASE}/v1/accounts/acc_FORCED/channels/find").mock(
        return_value=httpx.Response(
            200,
            json={"id": "-100777", "title": "Поищи на WB", "permissions": {"can_publish": True}},
        )
    )

    outcome = login_and_resolve_channel(
        cfg,
        tmp_path / "state.json",
        prompter=lambda _msg: "111111",
        skip_login_if_account_exists=False,
    )
    assert outcome.state.account_id == "acc_FORCED"
    assert outcome.reused_existing_account is False
