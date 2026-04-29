"""Runtime configuration for the bridge worker.

All settings are read from ``WBBRIDGE_*`` environment variables (or a
``.env`` file in the current working directory) and validated with
``pydantic-settings``.

Account / channel ids may also live in an ``admin_state.json`` file
written by ``wbpost login``; if that file exists at the configured
``WBBRIDGE_ADMIN_STATE_PATH`` it takes precedence over the env values
unless the env values are non-empty.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class BridgeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="WBBRIDGE_",
        extra="ignore",
    )

    # ---- WB parser side -------------------------------------------------
    parser_base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL of the WB parser API (no trailing slash).",
    )
    worker_id: str = Field(
        default="wb-bridge-1",
        description="Identifier the parser stores against locked posts.",
    )
    lock_ttl_seconds: int = Field(
        default=600,
        ge=30,
        description="How long the parser keeps a post locked while the bridge works on it.",
    )
    batch_size: int = Field(
        default=1,
        ge=1,
        le=10,
        description="How many posts to pull from the parser per cycle.",
    )
    publish_unplanned_posts: bool = Field(
        default=False,
        description=(
            "Whether the bridge loop may publish ready posts without planned_at. "
            "Production should keep this false so only scheduled posts are emitted."
        ),
    )

    # ---- MAX gateway side -----------------------------------------------
    maxapi_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of the MAX gateway API (no trailing slash).",
    )
    maxapi_token: str = Field(
        default="dev-token",
        description="Bearer token expected by the MAX gateway.",
    )
    maxapi_account_id: str = Field(
        default="",
        description="MAX account id (acc_*) the bridge publishes from. Required at runtime.",
    )
    maxapi_channel_id: str = Field(
        default="",
        description="Target MAX channel id. Required at runtime.",
    )

    # ---- Loop behaviour -------------------------------------------------
    poll_interval_seconds: float = Field(
        default=15.0,
        ge=1.0,
        description="Idle delay between cycles when the parser has no posts.",
    )
    request_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        description="Per-request HTTP timeout for both upstreams.",
    )

    log_level: str = Field(
        default="INFO",
        description="Root log level for the bridge process.",
    )

    admin_state_path: str = Field(
        default="../data/admin_state.json",
        description=(
            "Path (relative to bridge cwd or absolute) to the admin_state.json file "
            "written by wbpost login. If the file is present and account_id/channel_id "
            "env vars are empty, the bridge picks up ids from it on every cycle."
        ),
    )


def _load_admin_state(path: str) -> dict[str, str]:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if not candidate.exists():
        return {}
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - corrupt state should not crash bridge
        logger.warning("ignoring corrupt admin_state.json at %s: %s", candidate, exc)
        return {}
    return {
        "account_id": str(raw.get("account_id") or ""),
        "channel_id": str(raw.get("channel_id") or ""),
    }


def resolve_account_and_channel(settings: BridgeSettings) -> tuple[str, str]:
    """Return (account_id, channel_id), preferring env when non-empty, falling back to state file."""
    account_id = settings.maxapi_account_id
    channel_id = settings.maxapi_channel_id
    if account_id and channel_id:
        return account_id, channel_id
    state = _load_admin_state(settings.admin_state_path)
    return account_id or state.get("account_id", ""), channel_id or state.get("channel_id", "")


@lru_cache(maxsize=1)
def load_settings() -> BridgeSettings:
    return BridgeSettings()
