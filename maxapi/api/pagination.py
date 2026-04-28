"""Cursor-based pagination helpers."""

from __future__ import annotations

import base64
from collections.abc import Iterable
from typing import TypeVar

from api.errors import BadRequestError

T = TypeVar("T")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        offset = int(decoded)
    except (ValueError, UnicodeDecodeError):
        raise BadRequestError("Cursor is not valid.", code="invalid_cursor") from None
    if offset < 0:
        raise BadRequestError("Cursor is not valid.", code="invalid_cursor")
    return offset


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def paginate(items: Iterable[T], cursor: str | None, limit: int) -> tuple[list[T], str | None]:
    """Slice ``items`` using cursor/limit and return (page, next_cursor)."""
    materialized = list(items)
    offset = decode_cursor(cursor)
    end = offset + limit
    page = materialized[offset:end]
    next_cursor = encode_cursor(end) if end < len(materialized) else None
    return page, next_cursor
