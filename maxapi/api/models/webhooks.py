"""Webhook subscription models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from api.models.common import APIModel, PageMeta


class WebhookEventType(str, Enum):
    publication_accepted = "publication.accepted"
    publication_scheduled = "publication.scheduled"
    publication_published = "publication.published"
    publication_failed = "publication.failed"
    publication_cancelled = "publication.cancelled"
    metrics_collected = "metrics.collected"


class CreateWebhookSubscriptionRequest(APIModel):
    url: str
    secret: str | None = Field(default=None, min_length=16, max_length=256)
    events: list[WebhookEventType] = Field(min_length=1)


class WebhookSubscription(APIModel):
    subscription_id: str
    url: str
    events: list[WebhookEventType]
    created_at: datetime


class WebhookSubscriptionListResponse(PageMeta):
    items: list[WebhookSubscription] = Field(default_factory=list)
