"""Post publication models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from api.models.common import APIModel, PageMeta, ValidationIssue
from api.models.media import Media, MediaType


class TextFormat(str, Enum):
    plain = "plain"
    markdown = "markdown"
    html = "html"


class InlineButtonType(str, Enum):
    link = "link"
    callback = "callback"
    clipboard = "clipboard"


class InlineButton(APIModel):
    type: InlineButtonType
    text: str = Field(min_length=1, max_length=64)
    url: str | None = None
    payload: str | None = Field(default=None, max_length=1024)


class InlineKeyboard(APIModel):
    rows: list[list[InlineButton]] = Field(default_factory=list, max_length=30)


class MediaRef(APIModel):
    media_id: str | None = None
    url: str | None = None
    type: MediaType
    caption: str | None = Field(default=None, max_length=1024)
    position: int = Field(default=1, ge=1)


class PublishOptions(APIModel):
    disable_notification: bool = False
    pin_after_publish: bool = False
    remove_previous_pin: bool = False
    link_preview: bool = True
    dry_run: bool = False


class PublishPostRequest(APIModel):
    external_id: str | None = None
    title: str | None = Field(default=None, max_length=256)
    text: str = Field(min_length=1, max_length=4000)
    format: TextFormat = TextFormat.plain
    media: list[MediaRef] = Field(default_factory=list, max_length=10)
    inline_keyboard: InlineKeyboard | None = None
    options: PublishOptions | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EditPostRequest(APIModel):
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    format: TextFormat | None = None
    media: list[MediaRef] | None = Field(default=None, max_length=10)
    inline_keyboard: InlineKeyboard | None = None
    metadata: dict[str, Any] | None = None


class PinPostRequest(APIModel):
    notify_subscribers: bool = False


class PostValidationResult(APIModel):
    valid: bool
    normalized: PublishPostRequest
    warnings: list[ValidationIssue] = Field(default_factory=list)
    errors: list[ValidationIssue] = Field(default_factory=list)


class PublishedPostStatus(str, Enum):
    published = "published"
    edited = "edited"
    deleted = "deleted"


class PublishedPost(APIModel):
    message_id: str
    channel_id: str
    external_id: str | None = None
    status: PublishedPostStatus
    text: str | None = None
    format: TextFormat | None = None
    permalink: str | None = None
    media: list[Media] = Field(default_factory=list)
    pinned: bool = False
    published_at: datetime
    edited_at: datetime | None = None
    raw_response: dict[str, Any] | None = None


class PostListResponse(PageMeta):
    items: list[PublishedPost] = Field(default_factory=list)


class PostMetrics(APIModel):
    message_id: str
    views_count: int = Field(default=0, ge=0)
    reactions_total: int = Field(default=0, ge=0)
    reaction_breakdown: dict[str, int] = Field(default_factory=dict)
    comments_count: int = Field(default=0, ge=0)
    forwards_count: int = Field(default=0, ge=0)
    collected_at: datetime
