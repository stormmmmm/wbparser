"""Shared fixtures for the bridge tests.

The end-to-end bridge tests need both upstream FastAPI apps (the WB parser
and the MAX gateway) installed in the *same* virtualenv so we can run
their Python objects directly through ``httpx.ASGITransport``. The repo
root environment config takes care of that; locally:

    pip install -e ../parser -e ../maxapi -e ".[dev]"

These fixtures shape the parser's environment (isolated SQLite, outbox
under ``tmp_path``) the same way ``parser/tests/conftest.py`` does.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture()
def parser_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "wb_parser.db"
    outbox_file = tmp_path / "ready_posts.jsonl"
    inbox_file = tmp_path / "publication_results.jsonl"
    media_dir = tmp_path / "media_cache"
    raw_dir = tmp_path / "raw_cache"

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("OUTBOX_PATH", str(outbox_file))
    monkeypatch.setenv("INBOX_PUBLICATION_RESULTS_PATH", str(inbox_file))
    monkeypatch.setenv("MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("RAW_CACHE_DIR", str(raw_dir))
    monkeypatch.setenv("DEFAULT_POST_COOLDOWN_DAYS", "0")

    # Re-import parser modules under the patched env so cached settings /
    # engines pick up the temporary paths. We must import the models module
    # too — otherwise Base.metadata is empty and init_db() creates no tables.
    config = importlib.import_module("app.config")
    db_session = importlib.import_module("app.db.session")
    importlib.import_module("app.db.models")
    config.get_settings.cache_clear()
    db_session._ENGINE_CACHE.clear()
    db_session._SESSIONMAKER_CACHE.clear()
    db_session.init_db()
    yield


@pytest.fixture()
def maxapi_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MAXAPI_TOKEN", "test-token")
    # Force the in-process stub backend so the gateway never tries to call PyMax.
    monkeypatch.setenv("MAXAPI_BACKEND", "memory")
    yield


@pytest.fixture(autouse=True)
def _disable_user_dotenv(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make sure no developer-local ``.env`` leaks into BridgeSettings during tests."""
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        monkeypatch.setenv("WBBRIDGE_DOTENV_DISABLED", "1")
    for var in list(os.environ):
        if var.startswith("WBBRIDGE_"):
            monkeypatch.delenv(var, raising=False)
    yield
