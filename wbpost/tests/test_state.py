"""Tests for ``wbpost.state`` (admin_state.json read/write)."""

from __future__ import annotations

from datetime import datetime, timezone

from wbpost.state import AdminState, load_state, save_state, state_path_from


def test_save_and_reload_roundtrip(tmp_path):
    target = tmp_path / "state.json"
    state = AdminState(
        account_id="acc_TEST",
        channel_id="-1001234567890",
        channel_title="Поищи на WB",
        phone="+79991234567",
        logged_in_at=datetime(2026, 5, 1, 8, 30, tzinfo=timezone.utc),
    )
    save_state(target, state)
    loaded = load_state(target)
    assert loaded is not None
    assert loaded.account_id == state.account_id
    assert loaded.channel_id == state.channel_id
    assert loaded.channel_title == state.channel_title


def test_load_state_missing_returns_none(tmp_path):
    assert load_state(tmp_path / "no.json") is None


def test_state_path_relative_to_config(tmp_path):
    cfg = tmp_path / "subdir" / "admin.yml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("max: {}\n", encoding="utf-8")
    resolved = state_path_from(cfg, "../data/state.json")
    assert resolved == (tmp_path / "data" / "state.json").resolve()
