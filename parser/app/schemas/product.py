from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.utils.time import utcnow


class ParsedProduct(BaseModel):
    model_config = ConfigDict(extra="allow")

    article_id: str
    title: str
    normalized_title: str
    brand: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    subject_name: str | None = None
    tags: list[str] = Field(default_factory=list)

    price: int | None = None
    old_price: int | None = None
    discount_percent: int | None = None
    rating: float | None = None
    feedbacks_count: int | None = None
    orders_count: int | None = None
    popularity_score: float | None = None

    main_image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)

    product_url: str
    canonical_url: str
    affiliate_url: str | None = None

    availability: bool = True
    sizes_available: list[str] = Field(default_factory=list)
    colors_available: list[str] = Field(default_factory=list)

    seller_id: str | None = None
    seller_name: str | None = None

    source: str = "search"
    source_url: str | None = None

    detected_at: datetime = Field(default_factory=utcnow)
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_checked_at: datetime = Field(default_factory=utcnow)

    price_history: list[dict[str, Any]] = Field(default_factory=list)
    rating_history: list[dict[str, Any]] = Field(default_factory=list)
    review_summary: dict[str, Any] | None = None

    risk_flags: list[str] = Field(default_factory=list)
    reason_for_selection: str | None = None

    selection_score: float | None = None
    visual_score: float | None = None
    quality_score: float | None = None
    trend_score: float | None = None
    season_score: float | None = None

    raw_payload_ref: str | None = None


class ProductFilterResult(BaseModel):
    accepted: bool
    hard_reject: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    reject_reasons: list[str] = Field(default_factory=list)


class ProductScoreResult(BaseModel):
    article_id: str
    selection_score: float
    visual_score: float
    audience_fit_score: float
    price_score: float
    discount_score: float
    rating_score: float
    feedback_score: float
    trend_score: float
    season_score: float
    risk_penalty: float
    status: str
    reason_for_selection: str | None = None
