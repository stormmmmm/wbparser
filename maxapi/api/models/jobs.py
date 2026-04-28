"""Publication job and ReadyPost models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from api.models.common import APIModel, ErrorResponse, PageMeta
from api.models.posts import PublishedPost, PublishOptions
from api.models.schedules import ScheduledPost
from api.models.webhooks import WebhookEventType


class PublicationJobMode(str, Enum):
    auto = "auto"
    publish_now = "publish_now"
    schedule = "schedule"
    dry_run = "dry_run"


class PublicationJobStatus(str, Enum):
    accepted = "accepted"
    validating = "validating"
    importing_media = "importing_media"
    scheduled = "scheduled"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"


class ReadyPostMediaType(str, Enum):
    photo = "photo"
    image = "image"
    video = "video"
    document = "document"


class ReadyPostMedia(APIModel):
    position: int = Field(ge=1)
    article_id: str | None = None
    url: str
    local_path: str | None = None
    type: ReadyPostMediaType = ReadyPostMediaType.photo
    caption: str | None = None


class ReadyPostItem(APIModel):
    position: int = Field(ge=1)
    article_id: str
    title: str
    short_title: str | None = None
    price: int | None = None
    old_price: int | None = None
    discount_percent: int | None = None
    rating: float | None = None
    feedbacks_count: int | None = None
    displayed_url: str
    canonical_url: str | None = None
    affiliate_url: str | None = None
    main_image_url: str | None = None
    reason_for_selection: str | None = None
    risk_flags: list[str] = Field(default_factory=list)


class ReadyPostButton(APIModel):
    text: str = Field(min_length=1, max_length=64)
    url: str


class ReactionsHint(APIModel):
    enabled: bool = True
    text: str | None = None


class ReadyPostType(str, Enum):
    single = "single"
    collection = "collection"
    custom = "custom"


class ReadyPostParseMode(str, Enum):
    markdown = "markdown"
    html = "html"


class ReadyPost(APIModel):
    post_id: str
    post_type: ReadyPostType
    title: str
    text: str = Field(min_length=1)
    parse_mode: ReadyPostParseMode | None = None
    media: list[ReadyPostMedia] = Field(default_factory=list)
    items: list[ReadyPostItem] = Field(default_factory=list)
    buttons: list[ReadyPostButton] = Field(default_factory=list)
    reactions_hint: ReactionsHint | None = None
    planned_at: datetime | None = None
    fresh_until: datetime
    publication_status: str = "ready"
    created_at: datetime | None = None
    source: str = "wb_parser"
    version: str = "1"


class CreatePublicationJobRequest(APIModel):
    channel_id: str
    ready_post: ReadyPost
    mode: PublicationJobMode = PublicationJobMode.auto
    publish_at: datetime | None = None
    options: PublishOptions | None = None


class PublicationJob(APIModel):
    job_id: str
    account_id: str
    channel_id: str
    status: PublicationJobStatus
    ready_post: ReadyPost
    published_post: PublishedPost | None = None
    scheduled_post: ScheduledPost | None = None
    attempts: int = Field(default=0, ge=0)
    error: ErrorResponse | None = None
    created_at: datetime
    updated_at: datetime | None = None


class PublicationJobListResponse(PageMeta):
    items: list[PublicationJob] = Field(default_factory=list)


class PublicationEvent(APIModel):
    event_id: str
    event_type: WebhookEventType
    occurred_at: datetime
    job: PublicationJob
    metrics: PostMetrics | None = None  # forward ref resolved below
    signature: str | None = None


from api.models.posts import PostMetrics  # noqa: E402  (resolve forward ref)

PublicationEvent.model_rebuild()
