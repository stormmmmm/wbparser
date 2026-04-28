"""Persistent admin state — written by ``wbpost login`` and read by bridge."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AdminState(BaseModel):
    """State produced after a successful MAX login + channel resolution."""

    account_id: str
    channel_id: str
    channel_title: str | None = None
    phone: str | None = None
    logged_in_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def state_path_from(config_path: str | os.PathLike[str], state_rel: str) -> Path:
    base = Path(config_path).resolve().parent
    target = Path(state_rel)
    if target.is_absolute():
        return target
    return (base / target).resolve()


def load_state(path: str | os.PathLike[str]) -> AdminState | None:
    file = Path(path)
    if not file.exists():
        return None
    raw: dict[str, Any] = json.loads(file.read_text(encoding="utf-8"))
    return AdminState.model_validate(raw)


def save_state(path: str | os.PathLike[str], state: AdminState) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(state.model_dump_json(indent=2), encoding="utf-8")
