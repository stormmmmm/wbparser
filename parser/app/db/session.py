from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.base import Base

_ENGINE_CACHE: dict[str, Engine] = {}
_SESSIONMAKER_CACHE: dict[str, sessionmaker[Session]] = {}


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def get_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    if url not in _ENGINE_CACHE:
        connect_args = {"check_same_thread": False} if _is_sqlite(url) else {}
        _ENGINE_CACHE[url] = create_engine(url, connect_args=connect_args, future=True)
    return _ENGINE_CACHE[url]


def get_sessionmaker(database_url: str | None = None) -> sessionmaker[Session]:
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    if url not in _SESSIONMAKER_CACHE:
        _SESSIONMAKER_CACHE[url] = sessionmaker(
            bind=get_engine(url),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )
    return _SESSIONMAKER_CACHE[url]


def init_db(database_url: str | None = None) -> None:
    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope(database_url: str | None = None) -> Iterator[Session]:
    SessionLocal = get_sessionmaker(database_url)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
