"""Channel and permission models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from api.models.common import APIModel, PageMeta


class ChannelRole(str, Enum):
    owner = "owner"
    admin = "admin"
    editor = "editor"
    member = "member"
    unknown = "unknown"


class ChannelType(str, Enum):
    channel = "channel"
    chat = "chat"


class ChannelPermissions(APIModel):
    can_publish: bool
    can_edit: bool
    can_delete: bool
    can_pin: bool
    can_upload_media: bool = True
    reason: str | None = None


class Channel(APIModel):
    channel_id: str
    title: str
    username: str | None = None
    public_link: str | None = None
    type: ChannelType
    role: ChannelRole
    members_count: int | None = Field(default=None, ge=0)
    permissions: ChannelPermissions
    raw: dict[str, Any] | None = None


class ChannelListResponse(PageMeta):
    items: list[Channel] = Field(default_factory=list)
