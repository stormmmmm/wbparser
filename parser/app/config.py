from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "sqlite:///./wb_parser.db"
    WB_RATE_LIMIT_PER_SECOND: float = 2.0
    WB_REQUEST_TIMEOUT_SECONDS: int = 20
    WB_MAX_CONCURRENCY: int = 5

    AFFILIATE_ENABLED: bool = False
    AFFILIATE_BASE_URL: str = ""

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    OUTBOX_PATH: str = "../data/outbox/ready_posts.jsonl"
    INBOX_PUBLICATION_RESULTS_PATH: str = "../data/inbox/publication_results.jsonl"
    MEDIA_CACHE_DIR: str = "../data/media_cache"
    RAW_CACHE_DIR: str = "../data/raw_cache"

    POST_REVALIDATE_MINUTES: int = 15
    POST_LOCK_TTL_SECONDS: int = 600
    DEFAULT_POST_COOLDOWN_DAYS: int = 14

    LOG_LEVEL: str = "INFO"
    VERSION: str = "1"

    BASE_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])

    @property
    def parser_dir(self) -> Path:
        return self.BASE_DIR / "parser"

    @property
    def configs_dir(self) -> Path:
        return self.BASE_DIR / "configs"

    @property
    def outbox_path(self) -> Path:
        return self._resolve_from_parser(self.OUTBOX_PATH)

    @property
    def inbox_publication_results_path(self) -> Path:
        return self._resolve_from_parser(self.INBOX_PUBLICATION_RESULTS_PATH)

    @property
    def media_cache_dir(self) -> Path:
        return self._resolve_from_parser(self.MEDIA_CACHE_DIR)

    @property
    def raw_cache_dir(self) -> Path:
        return self._resolve_from_parser(self.RAW_CACHE_DIR)

    def _resolve_from_parser(self, value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        return (self.parser_dir / candidate).resolve()

    def ensure_paths(self) -> None:
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        self.inbox_publication_results_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_cache_dir.mkdir(parents=True, exist_ok=True)
        self.raw_cache_dir.mkdir(parents=True, exist_ok=True)

    def load_yaml_config(self, name: str, default: Any) -> Any:
        path = self.configs_dir / name
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if data is not None else default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_paths()
    return settings
