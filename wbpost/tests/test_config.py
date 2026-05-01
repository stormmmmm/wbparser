"""Tests for ``wbpost.config`` (admin.yml schema + env overrides)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wbpost.config import AdminConfig, load_admin_config


def _write_yaml(path: Path, body: dict) -> None:
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")


def test_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("PHONE_NUMBER", raising=False)
    cfg = load_admin_config(tmp_path / "missing.yml")
    assert isinstance(cfg, AdminConfig)
    assert cfg.max.channel_name == "Поищи на WB"
    assert len(cfg.schedule.slots) == 5
    assert cfg.schedule.slots[2].with_reaction_poll is True
    assert cfg.content.block_supplements_and_medical is True


def test_phone_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONE_NUMBER", "+79991234567")
    _write_yaml(tmp_path / "admin.yml", {"max": {"phone": ""}})
    cfg = load_admin_config(tmp_path / "admin.yml")
    assert cfg.max.phone == "+79991234567"


def test_explicit_phone_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONE_NUMBER", "+70000000000")
    _write_yaml(tmp_path / "admin.yml", {"max": {"phone": "+79998887766"}})
    cfg = load_admin_config(tmp_path / "admin.yml")
    assert cfg.max.phone == "+79998887766"


def test_env_override_can_set_nested_key(tmp_path, monkeypatch):
    monkeypatch.setenv("WBPOST_MAX__CHANNEL_NAME", "Test Channel")
    monkeypatch.setenv("WBPOST_SCHEDULE__ENABLED", "false")
    _write_yaml(tmp_path / "admin.yml", {"max": {"channel_name": "Original"}})
    cfg = load_admin_config(tmp_path / "admin.yml")
    assert cfg.max.channel_name == "Test Channel"
    assert cfg.schedule.enabled is False


def test_posting_minute_spread_supports_new_english_key(tmp_path):
    _write_yaml(tmp_path / "admin.yml", {"schedule": {"posting_minute_spread": "0, 15"}})
    cfg = load_admin_config(tmp_path / "admin.yml")
    assert cfg.schedule.posting_minute_spread == (0, 15)


def test_posting_minute_spread_supports_legacy_russian_key(tmp_path):
    _write_yaml(tmp_path / "admin.yml", {"schedule": {"разброс": "3, 9"}})
    cfg = load_admin_config(tmp_path / "admin.yml")
    assert cfg.schedule.posting_minute_spread == (3, 9)


def test_posting_minute_spread_supports_legacy_minute_spread_key(tmp_path):
    _write_yaml(tmp_path / "admin.yml", {"schedule": {"minute_spread": [3, 9]}})
    cfg = load_admin_config(tmp_path / "admin.yml")
    assert cfg.schedule.posting_minute_spread == (3, 9)


def test_invalid_slot_format_rejected(tmp_path):
    _write_yaml(
        tmp_path / "admin.yml",
        {"schedule": {"slots": [{"time": "25:00", "type": "single"}]}},
    )
    with pytest.raises(Exception):
        load_admin_config(tmp_path / "admin.yml")


def test_invalid_posting_minute_spread_rejected(tmp_path):
    _write_yaml(tmp_path / "admin.yml", {"schedule": {"posting_minute_spread": [15, 5]}})
    with pytest.raises(Exception):
        load_admin_config(tmp_path / "admin.yml")
