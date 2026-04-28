"""Runtime configuration for the bridge worker.

All settings are read from ``WBBRIDGE_*`` environment variables (or a
``.env`` file in the current working directory) and validated with
``pydantic-settings``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache(maxsize=1)
def load_settings() -> BridgeSettings:
    return BridgeSettings()
