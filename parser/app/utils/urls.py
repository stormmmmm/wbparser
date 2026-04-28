from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


ARTICLE_RE = re.compile(r"/catalog/(\d+)/detail\.aspx", flags=re.IGNORECASE)


def extract_article_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return text

    parsed = urlparse(text)
    if parsed.path:
        match = ARTICLE_RE.search(parsed.path)
        if match:
            return match.group(1)

    query = parse_qs(parsed.query)
    for key in ("article", "article_id", "nm", "nmId", "id"):
        candidate = query.get(key)
        if candidate and candidate[0].isdigit():
            return candidate[0]
    return None


def build_canonical_wb_url(article_id: str | int) -> str:
    return f"https://www.wildberries.ru/catalog/{article_id}/detail.aspx"


def canonicalize_wb_url(url_or_article: str | int | None) -> str | None:
    article_id = extract_article_id(url_or_article)
    if not article_id:
        return None
    return build_canonical_wb_url(article_id)
