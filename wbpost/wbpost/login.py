"""Interactive (and CI-friendly) MAX login flow."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable, Protocol

from .config import AdminConfig
from .maxapi_client import MaxApiClient
from .state import AdminState, save_state

DEFAULT_MAXAPI_TOKEN_ENV = "WBPOST_MAXAPI_TOKEN"
DEFAULT_MAXAPI_TOKEN_FALLBACK = "dev-token"


class CodePrompter(Protocol):
    def __call__(self, message: str) -> str:  # pragma: no cover - typing only
        ...


def stdin_prompter(message: str) -> str:
    """Default prompter — reads SMS code from stdin (TTY-friendly)."""
    print(message, end="", flush=True)
    line = sys.stdin.readline()
    if not line:
        raise EOFError("EOF reading SMS code from stdin")
    return line.strip()


def env_prompter(env_var: str = "WBPOST_SMS_CODE") -> CodePrompter:
    """CI prompter — picks code from env."""

    def _prompt(_message: str) -> str:
        value = os.environ.get(env_var, "")
        if not value:
            raise RuntimeError(
                f"non-interactive mode but env {env_var!r} is empty; "
                "set it to the expected SMS code or unset WBPOST_NONINTERACTIVE"
            )
        return value

    return _prompt


def get_default_prompter() -> CodePrompter:
    if os.environ.get("WBPOST_NONINTERACTIVE", "").strip() in ("1", "true", "yes"):
        return env_prompter()
    return stdin_prompter


@dataclass
class LoginOutcome:
    state: AdminState
    reused_existing_account: bool


def _find_existing_account(client: MaxApiClient, phone: str) -> str | None:
    try:
        accounts = client.list_accounts()
    except Exception:  # noqa: BLE001 - best-effort discovery
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    for acc in accounts:
        acc_phone = "".join(ch for ch in (acc.get("phone") or "") if ch.isdigit())
        if acc_phone and acc_phone == digits:
            return acc.get("id")
    return None


def login_and_resolve_channel(
    config: AdminConfig,
    state_file: str | os.PathLike[str],
    *,
    prompter: Callable[[str], str] | None = None,
    skip_login_if_account_exists: bool = True,
) -> LoginOutcome:
    """Run the full MAX login → find_channel → write-state pipeline."""
    if not config.max.phone:
        raise RuntimeError(
            "max.phone is empty — set it in admin.yml or via PHONE_NUMBER env var"
        )
    if not config.max.channel_name:
        raise RuntimeError("max.channel_name is empty — set it in admin.yml")

    prompt = prompter or get_default_prompter()
    reused = False
    api_key = os.environ.get(DEFAULT_MAXAPI_TOKEN_ENV) or DEFAULT_MAXAPI_TOKEN_FALLBACK

    with MaxApiClient(config.deployment.maxapi_url, api_key=api_key) as client:
        client.health()  # fail fast if gateway is down

        account_id: str | None = None
        if skip_login_if_account_exists:
            account_id = _find_existing_account(client, config.max.phone)
            reused = account_id is not None

        if account_id is None:
            challenge = client.start_login(
                phone=config.max.phone,
                device_name=config.max.device_name,
            )
            code = prompt(
                f"Введите 6-значный код из SMS, отправленный на {config.max.phone}: "
            )
            two_factor: str | None = None
            if challenge.requires_two_factor or config.max.two_factor_password:
                two_factor = (
                    config.max.two_factor_password
                    or prompt("Введите 2FA-пароль аккаунта: ")
                )
            verified = client.verify_login(
                challenge_id=challenge.challenge_id,
                code=code,
                two_factor_password=two_factor,
            )
            account_id = verified.account_id

        channel = client.find_channel(
            account_id=account_id,
            title=config.max.channel_name,
            title_match="exact",
        )
        state = AdminState(
            account_id=account_id,
            channel_id=channel.id,
            channel_title=channel.title,
            phone=config.max.phone,
        )
        save_state(state_file, state)
        return LoginOutcome(state=state, reused_existing_account=reused)
