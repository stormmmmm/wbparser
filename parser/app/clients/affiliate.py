from __future__ import annotations

from urllib.parse import quote_plus

from app.config import Settings


class AffiliateClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def build_url(self, canonical_url: str) -> str | None:
        if not self.settings.AFFILIATE_ENABLED:
            return None
        base = self.settings.AFFILIATE_BASE_URL.strip()
        if not base:
            return None
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}url={quote_plus(canonical_url)}"
