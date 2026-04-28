from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.db import session as db_session_module
from app.db.session import init_db, session_scope


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test.db"
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

    get_settings.cache_clear()
    db_session_module._ENGINE_CACHE.clear()
    db_session_module._SESSIONMAKER_CACHE.clear()
    init_db()
    yield


@pytest.fixture
def db_session(test_env):
    with session_scope() as session:
        yield session
