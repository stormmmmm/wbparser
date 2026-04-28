"""Backend protocol for MAX upstream operations.

The gateway keeps its own state (jobs, schedules, webhooks, idempotency cache),
but every action that touches a real MAX account session is delegated to a
backend. Two implementations ship out of the box:

* :class:`api.backends.memory.InMemoryBackend` — pure-Python stub used for
  development and tests.
* :class:`api.backends.pymax_backend.PyMaxBackend` — real userbot integration
  via ``maxapi-python`` (``pip install -e ".[pymax]"``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from api.models.channels import ChannelPermissions, ChannelRole, ChannelType
from api.models.media import MediaType
from api.models.posts import PublishOptions, PublishPostRequest


@dataclass
class LoginChallengeData:
    """Opaque per-login session data the backend hands back to the gateway."""

    challenge_id: str
    expires_at: datetime
    delivery: str = "sms"
    masked_destination: str | None = None
    backend_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class UpstreamChannel:
    """Channel descriptor as returned by the upstream backend."""

    channel_id: str
    title: str
    username: str | None
    public_link: str | None
    type: ChannelType
    role: ChannelRole
    members_count: int | None
    permissions: ChannelPermissions
    raw: dict[str, Any] | None = None


@dataclass
class UpstreamMedia:
    """Media descriptor stored gateway-side after upload/import."""

    media_id: str
    type: MediaType
    status: str
    url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    attachment: dict[str, Any] | None = None
    created_at: datetime | None = None


@dataclass
class UpstreamMessage:
    """Result of publishing/editing a message in the upstream channel."""

    message_id: str
    channel_id: str
    text: str | None
    permalink: str | None = None
    raw: dict[str, Any] | None = None


class MaxBackend(ABC):
    """Abstract upstream client used by the gateway."""

    # ---------------------------------------------------------------- login
    @abstractmethod
    async def start_login(
        self,
        *,
        phone: str,
        device_name: str,
        callback_url: str | None,
    ) -> LoginChallengeData:
        ...

    @abstractmethod
    async def verify_login(
        self,
        *,
        challenge_state: dict[str, Any],
        code: str,
        two_factor_password: str | None,
    ) -> dict[str, Any]:
        """Return account profile fields (max_user_id, username, display_name, ...)."""

    @abstractmethod
    async def logout(self, *, account_id: str) -> None:
        ...

    # -------------------------------------------------------------- channels
    @abstractmethod
    async def list_channels(self, *, account_id: str) -> list[UpstreamChannel]:
        ...

    @abstractmethod
    async def resolve_channel(
        self, *, account_id: str, link: str
    ) -> UpstreamChannel | None:
        ...

    # ----------------------------------------------------------------- media
    @abstractmethod
    async def upload_media(
        self,
        *,
        account_id: str,
        media_type: MediaType,
        content: bytes,
        filename: str | None,
        mime_type: str | None,
        caption: str | None,
    ) -> UpstreamMedia:
        ...

    @abstractmethod
    async def import_media(
        self,
        *,
        account_id: str,
        media_type: MediaType,
        url: str,
        filename: str | None,
        caption: str | None,
        source_post_id: str | None,
    ) -> UpstreamMedia:
        ...

    # ----------------------------------------------------------------- posts
    @abstractmethod
    async def publish_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        request: PublishPostRequest,
        options: PublishOptions | None,
        media: list[UpstreamMedia],
    ) -> UpstreamMessage:
        ...

    @abstractmethod
    async def edit_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_id: str,
        text: str | None,
        media: list[UpstreamMedia] | None,
    ) -> UpstreamMessage:
        ...

    @abstractmethod
    async def delete_message(
        self, *, account_id: str, channel_id: str, message_id: str
    ) -> None:
        ...

    @abstractmethod
    async def pin_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_id: str,
        notify: bool,
    ) -> None:
        ...

    @abstractmethod
    async def unpin_message(
        self, *, account_id: str, channel_id: str, message_id: str
    ) -> None:
        ...

    # ---------------------------------------------------------------- resume
    async def resume_existing_accounts(self) -> list[dict[str, Any]]:
        """Return persisted accounts the backend can re-attach on startup.

        Each entry must be a dict with at least ``account_id``, ``phone`` and
        ``display_name`` keys; optional ``max_user_id`` / ``username``. The
        gateway uses these to seed ``Storage`` so previously-authenticated
        users survive process restarts.
        """
        return []

    # --------------------------------------------------------------- shutdown
    async def aclose(self) -> None:
        """Release any background resources (websockets, sessions)."""
        return None
