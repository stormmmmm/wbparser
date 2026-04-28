"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query, Request

from api.backends import MaxBackend
from api.security import require_bearer_token
from api.storage import Storage


def get_storage(request: Request) -> Storage:
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        storage = Storage()
        request.app.state.storage = storage
    return storage


def get_backend(request: Request) -> MaxBackend:
    backend = getattr(request.app.state, "backend", None)
    if backend is None:  # pragma: no cover - main.create_app sets this
        from api.backends import InMemoryBackend

        backend = InMemoryBackend()
        request.app.state.backend = backend
    return backend


AuthDep = Annotated[str, Depends(require_bearer_token)]
StorageDep = Annotated[Storage, Depends(get_storage)]
BackendDep = Annotated[MaxBackend, Depends(get_backend)]


def idempotency_key_header(
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            description="Prevents duplicate publishing on retries.",
        ),
    ] = None,
) -> str | None:
    return idempotency_key


IdempotencyKeyDep = Annotated[str | None, Depends(idempotency_key_header)]


def limit_param(
    limit: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 50,
) -> int:
    return limit


def cursor_param(
    cursor: Annotated[str | None, Query(description="Opaque pagination cursor.")] = None,
) -> str | None:
    return cursor


LimitDep = Annotated[int, Depends(limit_param)]
CursorDep = Annotated[str | None, Depends(cursor_param)]
