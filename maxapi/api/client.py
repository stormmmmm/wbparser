"""High-level Python SDK for the MAX userbot.

The ``MaxUserBot`` class is the OOP equivalent of the FastAPI gateway: it
wraps the same :class:`~api.backends.MaxBackend` plus :class:`~api.storage.Storage`
that power the HTTP routes and exposes them as awaitable methods. Use this
when you want to drive the userbot directly from Python code without
running the FastAPI app — much like ``aiogram.Bot`` or
``pytelegrambotapi.TeleBot`` for Telegram.

Example
-------

.. code-block:: python

    import asyncio
    from api import MaxUserBot

    async def main() -> None:
        async with MaxUserBot(work_dir="./.maxapi-data") as bot:
            challenge = await bot.start_login(phone="+79991234567")
            account = await bot.verify_login(challenge.challenge_id, code=input("SMS code: "))

            channels = await bot.list_channels(account.account_id)
            target = next(c for c in channels if c.title == "Test")

            with open("images.jpg", "rb") as fh:
                photo = await bot.upload_media(
                    account.account_id, type="image", file=fh.read(), filename="images.jpg"
                )

            post = await bot.publish_post(
                account_id=account.account_id,
                channel_id=target.channel_id,
                text="🚀 [Открыть Google](https://google.com), а ещё **жирный**.",
                format="markdown",
                media=[photo, photo],  # two attachments referencing the same upload
            )
            print(post.message_id)

    asyncio.run(main())
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from api.backends import (
    InMemoryBackend,
    MaxBackend,
    UpstreamMedia,
    media_to_upstream,
)
from api.config import Settings, get_settings
from api.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from api.ids import new_id
from api.models.accounts import (
    Account,
    AccountSessionState,
    AccountStatus,
    LoginDelivery,
    StartLoginResponse,
)
from api.models.channels import Channel, ChannelPermissions, ChannelRole, ChannelType
from api.models.common import ValidationIssue
from api.models.health import HealthResponse, HealthStatus
from api.models.jobs import (
    CreatePublicationJobRequest,
    PublicationJob,
    PublicationJobMode,
    PublicationJobStatus,
    ReadyPost,
)
from api.models.media import Media, MediaImportRequest, MediaStatus, MediaType
from api.models.posts import (
    EditPostRequest,
    InlineKeyboard,
    MediaRef,
    PinPostRequest,
    PostMetrics,
    PostValidationResult,
    PublishedPost,
    PublishedPostStatus,
    PublishOptions,
    PublishPostRequest,
    TextFormat,
)
from api.models.schedules import (
    ScheduledPost,
    ScheduledPostStatus,
    SchedulePostRequest,
    UpdateScheduledPostRequest,
)
from api.models.webhooks import (
    CreateWebhookSubscriptionRequest,
    WebhookEventType,
    WebhookSubscription,
)
from api.storage import (
    Storage,
    materialize_media_from_refs,
    materialize_published_post,
    now_utc,
)

MediaInput = Media | MediaRef | str
"""A media reference accepted by :meth:`MaxUserBot.publish_post`.

Accepts:
* a :class:`~api.models.media.Media` returned from :meth:`upload_media` /
  :meth:`import_media`,
* a :class:`~api.models.posts.MediaRef` for full control,
* or a plain ``media_id`` string.
"""


def _channel_from_upstream(upstream) -> Channel:
    """Convert an :class:`~api.backends.UpstreamChannel` to the API model."""
    return Channel(
        channel_id=str(upstream.channel_id),
        title=upstream.title,
        username=upstream.username,
        public_link=upstream.public_link,
        type=upstream.type if isinstance(upstream.type, ChannelType) else ChannelType(upstream.type),
        role=upstream.role if isinstance(upstream.role, ChannelRole) else ChannelRole(upstream.role),
        members_count=upstream.members_count,
        permissions=upstream.permissions,
        raw=upstream.raw,
    )


def _media_from_upstream(upstream: UpstreamMedia, *, fallback_type: MediaType) -> Media:
    try:
        status_value = MediaStatus(upstream.status)
    except ValueError:
        status_value = MediaStatus.ready
    return Media(
        media_id=upstream.media_id,
        type=upstream.type or fallback_type,
        status=status_value,
        url=upstream.url,
        filename=upstream.filename,
        mime_type=upstream.mime_type,
        size_bytes=upstream.size_bytes,
        max_attachment=upstream.attachment,
        created_at=upstream.created_at or datetime.now(timezone.utc),
    )


def _coerce_format(value: TextFormat | str | None) -> TextFormat:
    if value is None:
        return TextFormat.plain
    if isinstance(value, TextFormat):
        return value
    return TextFormat(value)


def _coerce_media_type(value: MediaType | str) -> MediaType:
    if isinstance(value, MediaType):
        return value
    return MediaType(value)


def _coerce_media_refs(items: Iterable[MediaInput]) -> list[MediaRef]:
    """Normalize the variety of media inputs accepted by :meth:`publish_post`."""
    refs: list[MediaRef] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, MediaRef):
            refs.append(item)
        elif isinstance(item, Media):
            refs.append(
                MediaRef(
                    media_id=item.media_id,
                    type=item.type,
                    position=index,
                )
            )
        elif isinstance(item, str):
            refs.append(MediaRef(media_id=item, type=MediaType.image, position=index))
        else:  # pragma: no cover - defensive
            raise TypeError(
                "media items must be Media, MediaRef, or media_id str, got "
                f"{type(item).__name__}"
            )
    return refs


def _read_payload(file: bytes | str | os.PathLike[str]) -> tuple[bytes, str | None]:
    """Resolve the various ``file=`` shapes into ``(bytes, default_filename)``."""
    if isinstance(file, (bytes, bytearray)):
        return bytes(file), None
    path = Path(file)
    return path.read_bytes(), path.name


class MaxUserBot:
    """High-level async client for the MAX userbot.

    Mirrors every operation exposed by the FastAPI gateway, but executes
    in-process — no HTTP roundtrip, no bearer token. Authorization happens
    once at the upstream level via :meth:`start_login` + :meth:`verify_login`,
    and the session is persisted in ``work_dir`` (when using PyMax) so the
    same instance recovers from process restarts.

    Parameters
    ----------
    backend:
        Pre-built :class:`~api.backends.MaxBackend`. When omitted, the
        client picks one based on environment / explicit ``backend_name``:

        * ``"memory"`` — :class:`~api.backends.InMemoryBackend` (default
          for tests; deterministic, no I/O).
        * ``"pymax"`` — :class:`~api.backends.pymax_backend.PyMaxBackend`
          (real userbot, requires ``maxapi-python``).
    storage:
        Optional :class:`~api.storage.Storage` to share state with a
        running FastAPI app. When omitted a fresh one is created.
    backend_name:
        Override for the backend selection logic when ``backend`` is not
        provided. Defaults to ``settings.backend`` (env: ``MAXAPI_BACKEND``).
    work_dir, device_type, app_version:
        Forwarded to :class:`PyMaxBackend` when it is auto-instantiated.
    settings:
        Use a custom :class:`~api.config.Settings` instead of the
        process-wide cached one (mostly useful in tests).
    """

    def __init__(
        self,
        *,
        backend: MaxBackend | None = None,
        storage: Storage | None = None,
        backend_name: str | None = None,
        work_dir: str | os.PathLike[str] | None = None,
        device_type: str = "DESKTOP",
        app_version: str = "25.12.13",
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self.storage = storage or Storage()
        if backend is not None:
            self._backend: MaxBackend = backend
        else:
            self._backend = self._build_backend(
                backend_name=backend_name,
                work_dir=work_dir,
                device_type=device_type,
                app_version=app_version,
            )
        self._started = False

    # ------------------------------------------------------------------ infra
    def _build_backend(
        self,
        *,
        backend_name: str | None,
        work_dir: str | os.PathLike[str] | None,
        device_type: str,
        app_version: str,
    ) -> MaxBackend:
        name = (backend_name or self._settings.backend or "memory").lower()
        if name == "memory":
            return InMemoryBackend()
        if name == "pymax":
            from api.backends.pymax_backend import PyMaxBackend

            return PyMaxBackend(
                work_dir=str(work_dir) if work_dir else self._settings.pymax_work_dir,
                device_type=device_type,
                app_version=app_version,
            )
        raise ValueError(
            f"Unknown backend {name!r}; expected 'memory' or 'pymax'."
        )

    @property
    def backend(self) -> MaxBackend:
        """The underlying upstream backend (rarely needed by callers)."""
        return self._backend

    async def start(self) -> list[Account]:
        """Resume previously-stored sessions and seed the in-memory accounts.

        Mirrors what the FastAPI lifespan does on startup. Safe to call
        multiple times.
        """
        if self._started:
            return self.storage.list_accounts()
        self._started = True
        try:
            resumed = await self._backend.resume_existing_accounts()
        except Exception:  # pragma: no cover - resume best-effort
            resumed = []
        now = datetime.now(timezone.utc)
        for entry in resumed:
            account_id = entry.get("account_id")
            if not account_id:
                continue
            try:
                self.storage.get_account(account_id)
                continue
            except Exception:
                pass
            self.storage.add_account(
                Account(
                    account_id=account_id,
                    max_user_id=entry.get("max_user_id"),
                    phone_masked=_mask_phone(entry.get("phone") or ""),
                    username=entry.get("username"),
                    display_name=entry.get("display_name") or "MAX user",
                    status=AccountSessionState.connected,
                    last_activity_at=now,
                    created_at=now,
                )
            )
        return self.storage.list_accounts()

    async def close(self) -> None:
        """Close upstream sessions held by the backend.

        Calls :meth:`MaxBackend.close` if the backend supplies one. Does
        not log out the accounts on the MAX servers — that requires
        :meth:`logout`.
        """
        close = getattr(self._backend, "close", None)
        if callable(close):
            try:
                await close()
            except TypeError:  # pragma: no cover - sync close
                close()

    async def __aenter__(self) -> MaxUserBot:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # =========================================================== Health
    def health(self) -> HealthResponse:
        """Return the gateway's self-reported health.

        Pure local check; does not contact the upstream.
        """
        return HealthResponse(
            status=HealthStatus.ok,
            version=self._settings.api_version,
            time=datetime.now(timezone.utc),
        )

    # =========================================================== Accounts
    def list_accounts(self) -> list[Account]:
        """Return all locally-known accounts (connected or otherwise)."""
        return self.storage.list_accounts()

    async def start_login(
        self,
        phone: str,
        *,
        device_name: str = "wb-channel-poster",
        callback_url: str | None = None,
    ) -> StartLoginResponse:
        """Begin the phone-number + SMS-code login flow.

        Returns a :class:`StartLoginResponse` with a ``challenge_id`` you
        must pass to :meth:`verify_login` together with the SMS code.
        """
        data = await self._backend.start_login(
            phone=phone,
            device_name=device_name or "MAX user",
            callback_url=callback_url,
        )
        self.storage.register_challenge(
            challenge_id=data.challenge_id,
            phone=phone,
            device_name=device_name,
            callback_url=callback_url,
            expires_at=data.expires_at,
            backend_state=data.backend_state,
        )
        delivery_values = {d.value for d in LoginDelivery}
        return StartLoginResponse(
            challenge_id=data.challenge_id,
            expires_at=data.expires_at,
            delivery=LoginDelivery(data.delivery)
            if data.delivery in delivery_values
            else LoginDelivery.sms,
            masked_destination=data.masked_destination or _mask_phone(phone),
        )

    async def verify_login(
        self,
        challenge_id: str,
        code: str,
        *,
        two_factor_password: str | None = None,
    ) -> Account:
        """Consume an SMS code and return the connected :class:`Account`."""
        challenge = self.storage.pop_challenge(challenge_id)
        profile = await self._backend.verify_login(
            challenge_state=challenge.backend_state,
            code=code,
            two_factor_password=two_factor_password,
        )
        now = datetime.now(timezone.utc)
        account_id = new_id("acc")
        account = Account(
            account_id=account_id,
            max_user_id=profile.get("max_user_id"),
            phone_masked=_mask_phone(challenge.phone),
            username=profile.get("username"),
            display_name=profile.get("display_name")
            or challenge.device_name
            or "MAX user",
            status=AccountSessionState.connected,
            last_activity_at=now,
            created_at=now,
        )
        self.storage.add_account(account)
        pending = profile.get("_pymax_pending")
        if pending is not None and hasattr(self._backend, "attach_account"):
            await self._backend.attach_account(account_id=account_id, pending=pending)
        return account

    def get_account(self, account_id: str) -> Account:
        return self.storage.get_account(account_id)

    def get_account_status(self, account_id: str) -> AccountStatus:
        account = self.storage.get_account(account_id)
        can_publish = account.status == AccountSessionState.connected
        reason: str | None = None
        if account.status == AccountSessionState.rate_limited:
            reason = "Account is rate limited by MAX upstream."
        elif account.status == AccountSessionState.disabled:
            reason = "Account has been disabled."
        elif account.status != AccountSessionState.connected:
            reason = "Account session needs renewal."
        return AccountStatus(
            account_id=account.account_id,
            status=account.status,
            can_publish=can_publish,
            reason=reason,
            checked_at=datetime.now(timezone.utc),
        )

    async def logout(self, account_id: str) -> None:
        """Close the upstream session and forget the local account record."""
        self.storage.get_account(account_id)
        await self._backend.logout(account_id=account_id)
        self.storage.remove_account(account_id)

    # =========================================================== Channels
    async def list_channels(
        self,
        account_id: str,
        *,
        only_writable: bool = True,
        role: ChannelRole | str | None = None,
    ) -> list[Channel]:
        """Return all channels visible to the given account.

        Refreshes the local cache from the backend so the result is always
        up-to-date, regardless of whether the account has been used yet.
        """
        self.storage.get_account(account_id)
        upstream = await self._backend.list_channels(account_id=account_id)
        channels = [_channel_from_upstream(c) for c in upstream]
        for ch in channels:
            self.storage.upsert_channel(account_id, ch)
        if only_writable:
            channels = [c for c in channels if c.permissions.can_publish]
        if role is not None:
            target = role if isinstance(role, ChannelRole) else ChannelRole(role)
            channels = [c for c in channels if c.role == target]
        return channels

    async def resolve_channel(self, account_id: str, link: str) -> Channel:
        """Resolve a channel by ``@username``, public link, or numeric id."""
        self.storage.get_account(account_id)
        upstream = await self._backend.resolve_channel(account_id=account_id, link=link)
        if upstream is None:
            raise NotFoundError(
                "Channel could not be resolved.", code="channel_not_resolved"
            )
        channel = _channel_from_upstream(upstream)
        return self.storage.upsert_channel(account_id, channel)

    async def find_channels(
        self,
        account_id: str,
        *,
        title: str,
        exact: bool = True,
        case_insensitive: bool = True,
        only_writable: bool = False,
    ) -> list[Channel]:
        """Return every channel whose display title matches ``title``.

        ``exact=True`` requires the full title to equal the argument;
        ``exact=False`` performs a substring ("contains") match.
        ``case_insensitive`` defaults to ``True`` to make MAX's
        Cyrillic/Latin titles easy to match.

        The channel list is refreshed from the upstream on every call,
        so titles are always current.
        """
        channels = await self.list_channels(
            account_id, only_writable=only_writable
        )
        return _filter_channels_by_title(
            channels,
            title=title,
            exact=exact,
            case_insensitive=case_insensitive,
        )

    async def find_channel(
        self,
        account_id: str,
        *,
        title: str,
        exact: bool = True,
        case_insensitive: bool = True,
        only_writable: bool = False,
    ) -> Channel:
        """Return the **single** channel whose title matches ``title``.

        Raises :class:`~api.errors.NotFoundError` if no channel matches
        and :class:`~api.errors.ConflictError` if more than one does.
        """
        matches = await self.find_channels(
            account_id,
            title=title,
            exact=exact,
            case_insensitive=case_insensitive,
            only_writable=only_writable,
        )
        if not matches:
            raise NotFoundError(
                f"No channel found with title={title!r}.",
                code="channel_not_found",
            )
        if len(matches) > 1:
            raise ConflictError(
                f"Found {len(matches)} channels matching title={title!r}; "
                "refine with exact=True or inspect the full list via find_channels().",
                code="channel_title_ambiguous",
            )
        return matches[0]

    def get_channel(self, account_id: str, channel_id: str) -> Channel:
        return self.storage.get_channel(account_id, str(channel_id))

    def get_channel_permissions(
        self, account_id: str, channel_id: str
    ) -> ChannelPermissions:
        return self.storage.get_channel(account_id, str(channel_id)).permissions

    # =========================================================== Media
    async def upload_media(
        self,
        account_id: str,
        *,
        type: MediaType | str,
        file: bytes | str | os.PathLike[str],
        filename: str | None = None,
        caption: str | None = None,
        mime_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> Media:
        """Upload a binary file and return a reusable :class:`Media` record.

        ``file`` accepts raw ``bytes`` or a path-like object — paths are
        read off disk for you.
        """
        self.storage.get_account(account_id)
        media_type = _coerce_media_type(type)
        operation = f"sdk.uploadMedia:{account_id}"
        cached = self.storage.idempotency_get(operation, idempotency_key)
        if cached is not None:
            return cached
        body, default_name = _read_payload(file)
        if len(body) > self._settings.max_upload_bytes:
            raise ValidationFailedError(
                f"Uploaded media exceeds {self._settings.max_upload_bytes} bytes.",
                issues=[
                    ValidationIssue(
                        field="file", message="too_large", code="payload_too_large"
                    )
                ],
            )
        upstream = await self._backend.upload_media(
            account_id=account_id,
            media_type=media_type,
            content=body,
            filename=filename or default_name,
            mime_type=mime_type,
            caption=caption,
        )
        media = _media_from_upstream(upstream, fallback_type=media_type)
        self.storage.add_media(media)
        self.storage.idempotency_set(operation, idempotency_key, media)
        return media

    async def import_media(
        self,
        account_id: str,
        *,
        url: str,
        type: MediaType | str,
        filename: str | None = None,
        source_post_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Media:
        """Import a remote URL into a :class:`Media` record server-side."""
        self.storage.get_account(account_id)
        if not url.startswith(("http://", "https://")):
            raise ValidationFailedError(
                "Media URL must be HTTP/HTTPS.",
                issues=[
                    ValidationIssue(
                        field="url", message="invalid_scheme", code="invalid_media_url"
                    )
                ],
            )
        media_type = _coerce_media_type(type)
        operation = f"sdk.importMedia:{account_id}"
        cached = self.storage.idempotency_get(operation, idempotency_key)
        if cached is not None:
            return cached
        request = MediaImportRequest(
            url=url, type=media_type, filename=filename, source_post_id=source_post_id
        )
        upstream = await self._backend.import_media(
            account_id=account_id,
            media_type=request.type,
            url=request.url,
            filename=request.filename,
            caption=None,
            source_post_id=request.source_post_id,
        )
        media = _media_from_upstream(upstream, fallback_type=request.type)
        self.storage.add_media(media)
        self.storage.idempotency_set(operation, idempotency_key, media)
        return media

    def get_media(self, media_id: str) -> Media:
        return self.storage.get_media(media_id)

    # =========================================================== Posts
    async def publish_post(
        self,
        account_id: str,
        channel_id: str,
        *,
        text: str,
        format: TextFormat | str = TextFormat.plain,
        media: Sequence[MediaInput] = (),
        title: str | None = None,
        external_id: str | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        inline_keyboard: InlineKeyboard | None = None,
        disable_notification: bool = False,
        pin_after_publish: bool = False,
        remove_previous_pin: bool = False,
        link_preview: bool = True,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> PublishedPost:
        """Publish a post to the channel and return the materialized record.

        See the README's "Text formatting & word-links" section for the
        markdown subset (``[label](url)``, ``**bold**``, ``__underline__``,
        ``~~strike~~``, ``*italic*``).
        """
        await self._ensure_writable(account_id, str(channel_id))
        request = PublishPostRequest(
            external_id=external_id,
            title=title,
            text=text,
            format=_coerce_format(format),
            media=_coerce_media_refs(media),
            inline_keyboard=inline_keyboard,
            options=PublishOptions(
                disable_notification=disable_notification,
                pin_after_publish=pin_after_publish,
                remove_previous_pin=remove_previous_pin,
                link_preview=link_preview,
                dry_run=dry_run,
            ),
            source=source,
            metadata=metadata or {},
        )
        normalized = _normalize_publish_request(request)

        operation = f"sdk.publishPost:{account_id}:{channel_id}"
        cached = self.storage.idempotency_get(operation, idempotency_key)
        if cached is not None:
            return cached

        media_records = materialize_media_from_refs(self.storage, normalized.media)
        upstream_msg = await self._backend.publish_message(
            account_id=account_id,
            channel_id=str(channel_id),
            request=normalized,
            options=normalized.options,
            media=media_to_upstream(media_records),
        )
        post = materialize_published_post(
            channel_id=str(channel_id),
            request=normalized,
            options=normalized.options,
            media=media_records,
            message_id=upstream_msg.message_id,
            permalink=upstream_msg.permalink,
        )
        self.storage.add_post(account_id, post)
        self.storage.idempotency_set(operation, idempotency_key, post)
        return post

    async def publish(self, *args: Any, **kwargs: Any) -> PublishedPost:
        """Alias for :meth:`publish_post`."""
        return await self.publish_post(*args, **kwargs)

    def validate_post(
        self, account_id: str, channel_id: str, request: PublishPostRequest
    ) -> PostValidationResult:
        """Run gateway-side normalization without contacting the upstream."""
        self.storage.get_channel(account_id, str(channel_id))
        normalized = _normalize_publish_request(request)
        warnings: list[ValidationIssue] = []
        if normalized.text != request.text.strip() and request.text != normalized.text:
            warnings.append(
                ValidationIssue(
                    field="text",
                    message="Leading/trailing whitespace was trimmed.",
                    code="whitespace_trimmed",
                )
            )
        return PostValidationResult(
            valid=True, normalized=normalized, warnings=warnings, errors=[]
        )

    def list_posts(
        self,
        account_id: str,
        channel_id: str,
        *,
        since: datetime | None = None,
    ) -> list[PublishedPost]:
        return self.storage.list_posts(account_id, str(channel_id), since=since)

    def get_post(
        self, account_id: str, channel_id: str, message_id: str
    ) -> PublishedPost:
        return self.storage.get_post(account_id, str(channel_id), str(message_id))

    async def edit_post(
        self,
        account_id: str,
        channel_id: str,
        message_id: str,
        *,
        text: str | None = None,
        format: TextFormat | str | None = None,
        media: Sequence[MediaInput] | None = None,
        inline_keyboard: InlineKeyboard | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedPost:
        """Edit a published post.

        Pass ``media=[]`` to clear all attachments, or omit ``media`` to
        leave them untouched.
        """
        existing = self.storage.get_post(account_id, str(channel_id), str(message_id))
        update: dict[str, Any] = {
            "status": PublishedPostStatus.edited,
            "edited_at": now_utc(),
        }
        if text is not None:
            update["text"] = text
        if format is not None:
            update["format"] = _coerce_format(format)
        media_records: list[Media] | None = None
        if media is not None:
            request = EditPostRequest(
                text=text,
                format=_coerce_format(format) if format is not None else None,
                media=_coerce_media_refs(media),
                inline_keyboard=inline_keyboard,
                metadata=metadata,
            )
            assert request.media is not None
            media_records = materialize_media_from_refs(self.storage, request.media)
            update["media"] = media_records
        await self._backend.edit_message(
            account_id=account_id,
            channel_id=str(channel_id),
            message_id=str(message_id),
            text=text,
            media=media_to_upstream(media_records) if media_records is not None else None,
        )
        edited = existing.model_copy(update=update)
        return self.storage.replace_post(account_id, edited)

    async def delete_post(
        self, account_id: str, channel_id: str, message_id: str
    ) -> None:
        self.storage.get_post(account_id, str(channel_id), str(message_id))
        await self._backend.delete_message(
            account_id=account_id,
            channel_id=str(channel_id),
            message_id=str(message_id),
        )
        self.storage.delete_post(account_id, str(channel_id), str(message_id))

    async def pin_post(
        self,
        account_id: str,
        channel_id: str,
        message_id: str,
        *,
        notify_subscribers: bool = False,
    ) -> bool:
        post = self.storage.get_post(account_id, str(channel_id), str(message_id))
        await self._backend.pin_message(
            account_id=account_id,
            channel_id=str(channel_id),
            message_id=str(message_id),
            notify=bool(PinPostRequest(notify_subscribers=notify_subscribers).notify_subscribers),
        )
        self.storage.replace_post(account_id, post.model_copy(update={"pinned": True}))
        return True

    async def unpin_post(
        self, account_id: str, channel_id: str, message_id: str
    ) -> bool:
        post = self.storage.get_post(account_id, str(channel_id), str(message_id))
        try:
            await self._backend.unpin_message(
                account_id=account_id,
                channel_id=str(channel_id),
                message_id=str(message_id),
            )
        except Exception:
            # Best-effort, mirrors the FastAPI route behaviour.
            pass
        self.storage.replace_post(account_id, post.model_copy(update={"pinned": False}))
        return True

    def get_post_metrics(
        self, account_id: str, channel_id: str, message_id: str
    ) -> PostMetrics:
        post = self.storage.get_post(account_id, str(channel_id), str(message_id))
        return PostMetrics(
            message_id=post.message_id,
            views_count=0,
            reactions_total=0,
            reaction_breakdown={},
            comments_count=0,
            forwards_count=0,
            collected_at=datetime.now(timezone.utc),
        )

    # =========================================================== Schedules
    def list_scheduled(
        self,
        account_id: str,
        *,
        channel_id: str | None = None,
        status: ScheduledPostStatus | str | None = None,
    ) -> list[ScheduledPost]:
        status_value: ScheduledPostStatus | None
        if status is None or isinstance(status, ScheduledPostStatus):
            status_value = status
        else:
            status_value = ScheduledPostStatus(status)
        return self.storage.list_scheduled(
            account_id, channel_id=channel_id, status=status_value
        )

    def schedule_post(
        self,
        account_id: str,
        channel_id: str,
        publish_at: datetime,
        post: PublishPostRequest,
        *,
        timezone_name: str = "UTC",
        idempotency_key: str | None = None,
    ) -> ScheduledPost:
        if publish_at <= now_utc():
            raise ValidationFailedError(
                "publish_at must be in the future.",
                issues=[
                    ValidationIssue(
                        field="publish_at",
                        message="must be in the future",
                        code="publish_at_in_past",
                    )
                ],
            )
        request = SchedulePostRequest(
            channel_id=str(channel_id),
            publish_at=publish_at,
            post=post,
            timezone=timezone_name,
        )
        self.storage.get_channel(account_id, request.channel_id)

        def _build() -> ScheduledPost:
            return ScheduledPost(
                schedule_id=new_id("sch"),
                account_id=account_id,
                channel_id=request.channel_id,
                status=ScheduledPostStatus.scheduled,
                publish_at=request.publish_at,
                post=request.post,
                published_post=None,
                created_at=now_utc(),
                updated_at=None,
            )

        scheduled = self.storage.idempotent(
            f"sdk.schedulePost:{account_id}", idempotency_key, _build
        )
        return self.storage.add_scheduled(scheduled)

    def get_scheduled(self, account_id: str, schedule_id: str) -> ScheduledPost:
        return self.storage.get_scheduled(account_id, schedule_id)

    def update_scheduled(
        self,
        account_id: str,
        schedule_id: str,
        *,
        publish_at: datetime | None = None,
        post: PublishPostRequest | None = None,
    ) -> ScheduledPost:
        schedule = self.storage.get_scheduled(account_id, schedule_id)
        if schedule.status != ScheduledPostStatus.scheduled:
            raise ConflictError(
                "Scheduled post can only be modified while in 'scheduled' state.",
                code="schedule_not_modifiable",
            )
        request = UpdateScheduledPostRequest(publish_at=publish_at, post=post)
        update: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
        if request.publish_at is not None:
            if request.publish_at <= now_utc():
                raise ValidationFailedError(
                    "publish_at must be in the future.",
                    issues=[
                        ValidationIssue(
                            field="publish_at",
                            message="must be in the future",
                            code="publish_at_in_past",
                        )
                    ],
                )
            update["publish_at"] = request.publish_at
        if request.post is not None:
            update["post"] = request.post
        new_schedule = schedule.model_copy(update=update)
        return self.storage.replace_scheduled(new_schedule)

    def cancel_scheduled(self, account_id: str, schedule_id: str) -> None:
        schedule = self.storage.get_scheduled(account_id, schedule_id)
        if schedule.status == ScheduledPostStatus.published:
            raise ConflictError(
                "Already published scheduled posts cannot be cancelled."
            )
        self.storage.delete_scheduled(account_id, schedule_id)

    async def publish_scheduled_now(
        self,
        account_id: str,
        schedule_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> PublishedPost:
        schedule = self.storage.get_scheduled(account_id, schedule_id)
        if schedule.status not in (
            ScheduledPostStatus.scheduled,
            ScheduledPostStatus.failed,
        ):
            raise ConflictError("Schedule is not eligible for immediate publishing.")
        self.storage.get_channel(account_id, schedule.channel_id)

        operation = f"sdk.publishScheduledPostNow:{schedule_id}"
        cached = self.storage.idempotency_get(operation, idempotency_key)
        if cached is not None:
            return cached

        media_records = materialize_media_from_refs(
            self.storage, schedule.post.media
        )
        upstream_msg = await self._backend.publish_message(
            account_id=account_id,
            channel_id=schedule.channel_id,
            request=schedule.post,
            options=schedule.post.options,
            media=media_to_upstream(media_records),
        )
        published = materialize_published_post(
            channel_id=schedule.channel_id,
            request=schedule.post,
            options=schedule.post.options,
            media=media_records,
            message_id=upstream_msg.message_id,
            permalink=upstream_msg.permalink,
        )
        self.storage.add_post(account_id, published)
        self.storage.replace_scheduled(
            schedule.model_copy(
                update={
                    "status": ScheduledPostStatus.published,
                    "published_post": published,
                    "updated_at": now_utc(),
                }
            )
        )
        self.storage.idempotency_set(operation, idempotency_key, published)
        return published

    # =========================================================== Jobs
    def list_publication_jobs(
        self,
        account_id: str,
        *,
        status: PublicationJobStatus | str | None = None,
        source: str | None = None,
    ) -> list[PublicationJob]:
        status_value: PublicationJobStatus | None
        if status is None or isinstance(status, PublicationJobStatus):
            status_value = status
        else:
            status_value = PublicationJobStatus(status)
        return self.storage.list_jobs(account_id, status=status_value, source=source)

    async def create_publication_job(
        self,
        account_id: str,
        *,
        channel_id: str,
        ready_post: ReadyPost,
        mode: PublicationJobMode | str = PublicationJobMode.auto,
        publish_at: datetime | None = None,
        options: PublishOptions | None = None,
        idempotency_key: str | None = None,
    ) -> PublicationJob:
        if isinstance(mode, str):
            mode = PublicationJobMode(mode)
        request = CreatePublicationJobRequest(
            channel_id=str(channel_id),
            ready_post=ready_post,
            mode=mode,
            publish_at=publish_at,
            options=options,
        )
        self.storage.get_channel(account_id, request.channel_id)

        operation = f"sdk.createPublicationJob:{account_id}"
        cached = self.storage.idempotency_get(operation, idempotency_key)
        if cached is not None:
            return cached

        publish_request = _ready_post_to_publish_request(ready_post)
        applied_options = options or PublishOptions()
        now = now_utc()
        resolved_mode = _resolve_job_mode(request, applied_options)

        if resolved_mode == PublicationJobMode.dry_run:
            job = PublicationJob(
                job_id=new_id("job"),
                account_id=account_id,
                channel_id=request.channel_id,
                status=PublicationJobStatus.published,
                ready_post=ready_post,
                attempts=1,
                created_at=now,
                updated_at=now,
            )
        elif resolved_mode == PublicationJobMode.schedule:
            target_at = request.publish_at or ready_post.planned_at
            if target_at is None or target_at <= now:
                raise ConflictError(
                    "publish_at is required and must be in the future for scheduled jobs.",
                    code="invalid_schedule_time",
                )
            scheduled = ScheduledPost(
                schedule_id=new_id("sch"),
                account_id=account_id,
                channel_id=request.channel_id,
                status=ScheduledPostStatus.scheduled,
                publish_at=target_at,
                post=publish_request.model_copy(update={"options": applied_options}),
                created_at=now,
            )
            self.storage.add_scheduled(scheduled)
            job = PublicationJob(
                job_id=new_id("job"),
                account_id=account_id,
                channel_id=request.channel_id,
                status=PublicationJobStatus.scheduled,
                ready_post=ready_post,
                scheduled_post=scheduled,
                attempts=1,
                created_at=now,
                updated_at=now,
            )
        else:
            media_records = materialize_media_from_refs(
                self.storage, publish_request.media
            )
            upstream_msg = await self._backend.publish_message(
                account_id=account_id,
                channel_id=request.channel_id,
                request=publish_request,
                options=applied_options,
                media=media_to_upstream(media_records),
            )
            published = materialize_published_post(
                channel_id=request.channel_id,
                request=publish_request,
                options=applied_options,
                media=media_records,
                message_id=upstream_msg.message_id,
                permalink=upstream_msg.permalink,
            )
            self.storage.add_post(account_id, published)
            job = PublicationJob(
                job_id=new_id("job"),
                account_id=account_id,
                channel_id=request.channel_id,
                status=PublicationJobStatus.published,
                ready_post=ready_post,
                published_post=published,
                attempts=1,
                created_at=now,
                updated_at=now,
            )

        self.storage.idempotency_set(operation, idempotency_key, job)
        return self.storage.add_job(job)

    def get_publication_job(self, account_id: str, job_id: str) -> PublicationJob:
        return self.storage.get_job(account_id, job_id)

    def cancel_publication_job(
        self, account_id: str, job_id: str
    ) -> PublicationJob:
        job = self.storage.get_job(account_id, job_id)
        if job.status in (
            PublicationJobStatus.published,
            PublicationJobStatus.failed,
            PublicationJobStatus.cancelled,
            PublicationJobStatus.expired,
        ):
            raise ConflictError(
                "Job is not in a cancellable state.", code="job_not_cancellable"
            )
        cancelled = job.model_copy(
            update={
                "status": PublicationJobStatus.cancelled,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return self.storage.replace_job(cancelled)

    # =========================================================== Webhooks
    def list_webhooks(self) -> list[WebhookSubscription]:
        return self.storage.list_webhooks()

    def add_webhook(
        self,
        url: str,
        events: Iterable[WebhookEventType | str],
        *,
        secret: str | None = None,
    ) -> WebhookSubscription:
        normalized_events = [
            e if isinstance(e, WebhookEventType) else WebhookEventType(e)
            for e in events
        ]
        request = CreateWebhookSubscriptionRequest(
            url=url, secret=secret, events=normalized_events
        )
        subscription = WebhookSubscription(
            subscription_id=new_id("hook"),
            url=request.url,
            events=request.events,
            created_at=now_utc(),
        )
        return self.storage.add_webhook(subscription)

    def remove_webhook(self, subscription_id: str) -> None:
        self.storage.remove_webhook(subscription_id)

    # ------------------------------------------------------------------ helpers
    async def _ensure_writable(self, account_id: str, channel_id: str) -> None:
        try:
            channel = self.storage.get_channel(account_id, channel_id)
        except Exception:
            self.storage.get_account(account_id)
            upstream = await self._backend.resolve_channel(
                account_id=account_id, link=channel_id
            )
            if upstream is None:
                for candidate in await self._backend.list_channels(
                    account_id=account_id
                ):
                    if str(candidate.channel_id) == str(channel_id):
                        upstream = candidate
                        break
            if upstream is None:
                raise NotFoundError(
                    "Channel not found for this account.", code="channel_not_found"
                ) from None
            channel = self.storage.upsert_channel(
                account_id, _channel_from_upstream(upstream)
            )
        if not channel.permissions.can_publish:
            raise ForbiddenError("Account cannot publish to this channel.")


# --------------------------------------------------------------------- helpers
def _filter_channels_by_title(
    channels: list[Channel],
    *,
    title: str,
    exact: bool,
    case_insensitive: bool,
) -> list[Channel]:
    needle = title.strip()
    needle_cmp = needle.casefold() if case_insensitive else needle

    def _match(channel: Channel) -> bool:
        haystack = channel.title or ""
        hay = haystack.casefold() if case_insensitive else haystack
        return hay == needle_cmp if exact else needle_cmp in hay

    return [c for c in channels if _match(c)]


def _normalize_publish_request(
    payload: PublishPostRequest,
) -> PublishPostRequest:
    text = payload.text.strip()
    if len(text) > 4000:
        raise ValidationFailedError(
            "Post text exceeds the 4000-character limit.",
            issues=[ValidationIssue(field="text", message="too_long", code="text_too_long")],
        )
    return payload.model_copy(update={"text": text})


def _ready_post_to_publish_request(ready_post: ReadyPost) -> PublishPostRequest:
    fmt = TextFormat.plain
    if ready_post.parse_mode is not None:
        fmt = TextFormat(ready_post.parse_mode.value)
    return PublishPostRequest(
        external_id=ready_post.post_id,
        title=ready_post.title,
        text=ready_post.text,
        format=fmt,
        media=[
            MediaRef(
                url=item.url,
                type=_coerce_media_type(
                    {"photo": "image"}.get(
                        item.type.value if hasattr(item.type, "value") else str(item.type),
                        item.type.value if hasattr(item.type, "value") else str(item.type),
                    )
                ),
                caption=item.caption,
                position=item.position,
            )
            for item in ready_post.media
        ],
        inline_keyboard=None,
        options=None,
        source=ready_post.source,
        metadata={
            "post_type": ready_post.post_type.value,
            "fresh_until": ready_post.fresh_until.isoformat(),
            "version": ready_post.version,
        },
    )


def _resolve_job_mode(
    request: CreatePublicationJobRequest, options: PublishOptions
) -> PublicationJobMode:
    if options.dry_run:
        return PublicationJobMode.dry_run
    if request.mode != PublicationJobMode.auto:
        return request.mode
    if request.publish_at is not None or request.ready_post.planned_at is not None:
        return PublicationJobMode.schedule
    return PublicationJobMode.publish_now


def _mask_phone(phone: str) -> str | None:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 4:
        return None
    masked_body = "*" * max(len(digits) - 5, 1)
    return f"+{digits[0]}{masked_body}{digits[-4:]}"


__all__ = ["MaxUserBot", "MediaInput"]
