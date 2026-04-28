from __future__ import annotations

import re
from typing import Iterable


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s-]+", flags=re.UNICODE)


def normalize_title(value: str) -> str:
    value = (value or "").strip().lower()
    value = _PUNCT_RE.sub(" ", value)
    value = _WS_RE.sub(" ", value)
    return value.strip()


def short_title(value: str, max_len: int = 48) -> str:
    value = _WS_RE.sub(" ", (value or "").strip())
    if len(value) <= max_len:
        return value
    trimmed = value[: max_len - 1].rstrip()
    return f"{trimmed}…"


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    haystack = normalize_title(text)
    return any(normalize_title(word) in haystack for word in keywords)
