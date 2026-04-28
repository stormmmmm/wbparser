"""Loader and schema for ``admin.yml`` — the single admin-facing config."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path("admin.yml")
ENV_PREFIX = "WBPOST_"


class MaxConfig(BaseModel):
    phone: str = ""
    channel_name: str = "Поищи на WB"
    device_name: str = "wbpost-bridge"
    two_factor_password: str = ""

    @field_validator("phone")
    @classmethod
    def _phone_or_env(cls, value: str) -> str:
        if value:
            return value
        return os.environ.get("PHONE_NUMBER", "")


_SLOT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleSlot(BaseModel):
    time: str
    type: Literal["single", "collection"]
    with_reaction_poll: bool = False

    @field_validator("time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        if not _SLOT_TIME_RE.match(value):
            raise ValueError(f"slot time must be HH:MM (00:00–23:59), got {value!r}")
        return value


class ScheduleConfig(BaseModel):
    timezone: str = "Europe/Moscow"
    enabled: bool = True
    slots: list[ScheduleSlot] = Field(
        default_factory=lambda: [
            ScheduleSlot(time="10:00", type="collection"),
            ScheduleSlot(time="12:00", type="single"),
            ScheduleSlot(time="14:00", type="collection", with_reaction_poll=True),
            ScheduleSlot(time="16:00", type="collection"),
            ScheduleSlot(time="19:00", type="single"),
        ]
    )


class AffiliateConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""


class ContentConfig(BaseModel):
    block_supplements_and_medical: bool = True
    affiliate: AffiliateConfig = Field(default_factory=AffiliateConfig)


class DeploymentConfig(BaseModel):
    parser_url: str = "http://parser:8000"
    maxapi_url: str = "http://maxapi:8080"
    state_path: str = "data/admin_state.json"


class AdminConfig(BaseModel):
    max: MaxConfig = Field(default_factory=MaxConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    content: ContentConfig = Field(default_factory=ContentConfig)
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)


def _apply_env_overrides(data: dict) -> dict:
    """Apply WBPOST_<SECTION>__<KEY> overrides to a parsed admin dict."""
    out = dict(data)
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        cursor: dict = out
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):
                # the env var collides with a non-mapping leaf; ignore.
                cursor = {}
                break
        leaf = path[-1]
        if value.lower() in ("true", "false"):
            cursor[leaf] = value.lower() == "true"
        elif value.isdigit():
            cursor[leaf] = int(value)
        else:
            cursor[leaf] = value
    return out


def load_admin_config(path: str | os.PathLike[str] | None = None) -> AdminConfig:
    """Read admin.yml from disk (or env-only) and validate it."""
    candidate = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict = {}
    if candidate.exists():
        with candidate.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    raw = _apply_env_overrides(raw)
    return AdminConfig.model_validate(raw)


def find_admin_config_path(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Walk up from *start* looking for an admin.yml file."""
    here = Path(start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        for name in ("admin.yml", "admin.yaml"):
            target = candidate / name
            if target.exists():
                return target
    return None
