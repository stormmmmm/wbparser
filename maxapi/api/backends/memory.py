"""In-memory backend used as a development/test stub.

Behavioural notes:

* ``verify_login`` accepts ``code == "000000"`` to make scripted tests trivial.
* ``resolve_channel`` matches against the gateway's seeded demo channel.
* All operations are pure-Python and do not touch the network.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from api.backends.protocol import (
    LoginChallengeData,
    MaxBackend,
    UpstreamChannel,
    UpstreamMedia,
    UpstreamMessage,
)
from api.errors import ConflictError, NotFoundError
from api.ids import new_id, new_message_id
from api.models.channels import ChannelPermissions, ChannelRole, ChannelType
from api.models.media import MediaType
from api.models.posts import PublishOptions, PublishPostRequest


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SEED_CHANNEL = UpstreamChannel(
    channel_id="-1001111111111",
    title="WB Finds (demo)",
    username="wb_finds_demo",
    public_link="https://max.ru/wb_finds_demo",
    type=ChannelType.channel,
    role=ChannelRole.owner,
    members_count=12345,
    permissions=ChannelPermissions(
        can_publish=True,
        can_edit=True,
        can_delete=True,
        can_pin=True,
        can_upload_media=True,
    ),
)


class InMemoryBackend(MaxBackend):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._challenges: dict[str, dict[str, Any]] = {}
        # account_id -> set of channel ids (always includes the seed channel).
        self._channels: dict[str, list[UpstreamChannel]] = {}

    # ----------------------------------------------------------------- login
    async def start_login(
        self,
        *,
        phone: str,
        device_name: str,
        callback_url: str | None,
    ) -> LoginChallengeData:
        challenge_id = new_id("chg")
        expires = _utcnow() + timedelta(minutes=10)
        async with self._lock:
            self._challenges[challenge_id] = {
                "phone": phone,
                "device_name": device_name,
                "callback_url": callback_url,
                "expires_at": expires,
                "expected_code": "000000",
            }
        return LoginChallengeData(
            challenge_id=challenge_id,
            expires_at=expires,
            delivery="sms",
            masked_destination=_mask_phone(phone),
            backend_state={"challenge_id": challenge_id},
        )

    async def verify_login(
        self,
        *,
        challenge_state: dict[str, Any],
        code: str,
        two_factor_password: str | None,
    ) -> dict[str, Any]:
        challenge_id = challenge_state.get("challenge_id")
        async with self._lock:
            challenge = self._challenges.get(challenge_id) if challenge_id else None
            if challenge is None:
                raise NotFoundError("Login challenge not found.", code="challenge_not_found")
            if _utcnow() > challenge["expires_at"]:
                self._challenges.pop(challenge_id, None)
                raise ConflictError("Login challenge has expired.", code="challenge_expired")
            if code != challenge["expected_code"]:
                raise ConflictError("Verification code is incorrect.", code="invalid_code")
            self._challenges.pop(challenge_id, None)
            return {
                "phone": challenge["phone"],
                "device_name": challenge["device_name"],
                "max_user_id": None,
                "username": None,
                "display_name": challenge["device_name"] or "MAX user",
            }

    async def logout(self, *, account_id: str) -> None:
        async with self._lock:
            self._channels.pop(account_id, None)

    # -------------------------------------------------------------- channels
    async def list_channels(self, *, account_id: str) -> list[UpstreamChannel]:
        async with self._lock:
            channels = self._channels.get(account_id, [])
            if not channels:
                # Seed every account with the same demo channel so the gateway
                # has something to talk about out of the box.
                self._channels[account_id] = [_SEED_CHANNEL]
                return [_SEED_CHANNEL]
            return list(channels)

    async def resolve_channel(
        self, *, account_id: str, link: str
    ) -> UpstreamChannel | None:
        normalized = link.lstrip("@").strip().lower()
        for channel in await self.list_channels(account_id=account_id):
            if channel.username and channel.username.lower() == normalized:
                return channel
            if channel.public_link and normalized in channel.public_link.lower():
                return channel
            if channel.channel_id == link:
                return channel
        return None

    # ----------------------------------------------------------------- media
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
        return UpstreamMedia(
            media_id=new_id("med"),
            type=media_type,
            status="uploaded",
            url=None,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            attachment={"caption": caption} if caption else None,
            created_at=_utcnow(),
        )

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
        return UpstreamMedia(
            media_id=new_id("med"),
            type=media_type,
            status="ready",
            url=url,
            filename=filename,
            mime_type=None,
            size_bytes=None,
            attachment={"source_post_id": source_post_id} if source_post_id else None,
            created_at=_utcnow(),
        )

    # ----------------------------------------------------------------- posts
    async def publish_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        request: PublishPostRequest,
        options: PublishOptions | None,
        media: list[UpstreamMedia],
    ) -> UpstreamMessage:
        return UpstreamMessage(
            message_id=new_message_id(),
            channel_id=channel_id,
            text=request.text,
            permalink=None,
            raw=None,
        )

    async def edit_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_id: str,
        text: str | None,
        media: list[UpstreamMedia] | None,
    ) -> UpstreamMessage:
        return UpstreamMessage(
            message_id=message_id,
            channel_id=channel_id,
            text=text,
            permalink=None,
            raw=None,
        )

    async def delete_message(
        self, *, account_id: str, channel_id: str, message_id: str
    ) -> None:
        return None

    async def pin_message(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_id: str,
        notify: bool,
    ) -> None:
        return None

    async def unpin_message(
        self, *, account_id: str, channel_id: str, message_id: str
    ) -> None:
        return None


def _mask_phone(phone: str) -> str | None:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 4:
        return None
    masked_body = "*" * max(len(digits) - 5, 1)
    return f"+{digits[0]}{masked_body}{digits[-4:]}"
