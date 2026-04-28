from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.utils.time import utcnow


class ReadyPostMedia(BaseModel):
    position: int
    article_id: str | None = None
    url: str
    local_path: str | None = None
    type: str = "photo"
    caption: str | None = None


class ReadyPostItem(BaseModel):
    position: int
    article_id: str
    title: str
    short_title: str
    price: int | None = None
    old_price: int | None = None
    discount_percent: int | None = None
    rating: float | None = None
    feedbacks_count: int | None = None
    displayed_url: str
    canonical_url: str
    affiliate_url: str | None = None
    main_image_url: str | None = None
    reason_for_selection: str | None = None
    risk_flags: list[str] = Field(default_factory=list)


class ReadyPostButton(BaseModel):
    text: str
    url: str


class ReactionsHint(BaseModel):
    enabled: bool = True
    text: str = "Да - ❤️     Нет - 🔥"


class ReadyPost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    post_id: str | int
    post_type: str
    title: str
    text: str
    parse_mode: str | None = None
    media: list[ReadyPostMedia] = Field(default_factory=list)
    items: list[ReadyPostItem] = Field(default_factory=list)
    buttons: list[ReadyPostButton] = Field(default_factory=list)
    reactions_hint: ReactionsHint | None = None
    planned_at: datetime | None = None
    fresh_until: datetime
    publication_status: str = "ready"
    created_at: datetime = Field(default_factory=utcnow)
    source: str = "wb_parser"
    version: str = "1"
