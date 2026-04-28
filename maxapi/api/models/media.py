"""Media upload/import models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from api.models.common import APIModel


class MediaType(str, Enum):
    image = "image"
    video = "video"
    audio = "audio"
    document = "document"


class MediaStatus(str, Enum):
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class MediaUploadRequest(APIModel):
    """Body schema for multipart uploads.

    The actual binary file is parsed via FastAPI's ``UploadFile``;
    this model exists so the OpenAPI surface stays in sync.
    """

    type: MediaType
    filename: str | None = None
    caption: str | None = Field(default=None, max_length=1024)


class MediaImportRequest(APIModel):
    url: str
    type: MediaType
    filename: str | None = None
    caption: str | None = Field(default=None, max_length=1024)
    source_post_id: str | None = None


class Media(APIModel):
    media_id: str
    type: MediaType
    status: MediaStatus
    url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    max_attachment: dict[str, Any] | None = None
    created_at: datetime
