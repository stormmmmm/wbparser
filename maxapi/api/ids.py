"""Identifier generation helpers."""

from __future__ import annotations

import secrets
import string

_ALPHABET = string.digits + string.ascii_uppercase
_ID_LENGTH = 26


def _new_token(length: int = _ID_LENGTH) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def new_id(prefix: str) -> str:
    """Return a sortable-ish identifier with the given prefix."""
    return f"{prefix}_{_new_token()}"


def new_message_id() -> str:
    """Return a MAX message identifier (mid.<digits>)."""
    return "mid." + "".join(secrets.choice(string.digits) for _ in range(15))


def new_event_id() -> str:
    return new_id("evt")
