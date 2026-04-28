"""Scheduled post models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from api.models.common import APIModel, PageMeta
from api.models.posts import PublishedPost, PublishPostRequest


class ScheduledPostStatus(str, Enum):
    scheduled = "scheduled"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"


class SchedulePostRequest(APIModel):
    channel_id: str
    publish_at: datetime
    post: PublishPostRequest
    timezone: str = "UTC"


class UpdateScheduledPostRequest(APIModel):
    publish_at: datetime | None = None
    post: PublishPostRequest | None = None


class ScheduledPost(APIModel):
    schedule_id: str
    account_id: str
    channel_id: str
    status: ScheduledPostStatus
    publish_at: datetime
    post: PublishPostRequest
    published_post: PublishedPost | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ScheduledPostListResponse(PageMeta):
    items: list[ScheduledPost] = Field(default_factory=list)
