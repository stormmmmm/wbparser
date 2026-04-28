from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PostLockIn(BaseModel):
    worker_id: str
    lock_ttl_seconds: int = 600


class PostPublishedIn(BaseModel):
    telegram_message_id: str
    telegram_url: str | None = None
    published_at: datetime
    raw_response: dict[str, Any] = Field(default_factory=dict)


class PostFailedIn(BaseModel):
    error_code: str
    error_message: str
    retryable: bool = True


class PostMetricsIn(BaseModel):
    collected_at: datetime
    views_count: int = 0
    reactions_total: int = 0
    reaction_breakdown: dict[str, int] = Field(default_factory=dict)
    forwards_count: int = 0
    comments_count: int = 0


class ClickMetricIn(BaseModel):
    post_id: str | int
    article_id: str
    clicked_at: datetime
    click_source: str | None = None
    redirect_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class PostPublicationResultIn(BaseModel):
    post_id: str | int
    status: str
    telegram_message_id: str | None = None
    telegram_url: str | None = None
    published_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = True
    raw_response: dict[str, Any] = Field(default_factory=dict)
