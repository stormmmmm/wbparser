"""In-memory storage backing the userbot gateway state.

The MAX userbot gateway is intended to wrap an authorized MAX user
account. Real installations will plug in upstream MAX clients and a
durable database; this module provides a deterministic, dependency-free
backend that fully satisfies the OpenAPI contract for development,
local testing, and CI.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any, TypeVar

from api.errors import ConflictError, NotFoundError
from api.ids import new_id, new_message_id
from api.models.accounts import Account, AccountSessionState
from api.models.channels import (
    Channel,
    ChannelPermissions,
    ChannelRole,
    ChannelType,
)
from api.models.jobs import (
    PublicationJob,
    PublicationJobStatus,
)
from api.models.media import Media, MediaStatus, MediaType
from api.models.posts import (
    InlineKeyboard,
    MediaRef,
    PublishedPost,
    PublishedPostStatus,
    PublishOptions,
    PublishPostRequest,
    TextFormat,
)
from api.models.schedules import ScheduledPost, ScheduledPostStatus
from api.models.webhooks import WebhookSubscription

T = TypeVar("T")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LoginChallenge:
    """Pending interactive login attempt for an account owner.

    Verification itself is delegated to the configured backend (see
    :class:`api.backends.MaxBackend`); this object only keeps gateway-side
    bookkeeping plus an opaque ``backend_state`` payload.
    """

    def __init__(
        self,
        challenge_id: str,
        phone: str,
        device_name: str | None,
        callback_url: str | None,
        expires_at: datetime,
        backend_state: dict[str, Any] | None = None,
    ) -> None:
        self.challenge_id = challenge_id
        self.phone = phone
        self.device_name = device_name
        self.callback_url = callback_url
        self.expires_at = expires_at
        self.backend_state: dict[str, Any] = backend_state or {}


class Storage:
    """Thread-safe in-memory state for the gateway."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.accounts: OrderedDict[str, Account] = OrderedDict()
        self.channels: OrderedDict[str, Channel] = OrderedDict()
        # account_id -> set of channel_ids
        self.account_channels: dict[str, set[str]] = {}
        self.media: OrderedDict[str, Media] = OrderedDict()
        # (account_id, channel_id, message_id) -> PublishedPost
        self.posts: OrderedDict[tuple[str, str, str], PublishedPost] = OrderedDict()
        self.scheduled_posts: OrderedDict[str, ScheduledPost] = OrderedDict()
        self.jobs: OrderedDict[str, PublicationJob] = OrderedDict()
        self.webhooks: OrderedDict[str, WebhookSubscription] = OrderedDict()
        self.login_challenges: dict[str, LoginChallenge] = {}
        # Idempotency-Key -> (operation, response payload)
        self.idempotency: dict[tuple[str, str], Any] = {}

        self._seed()

    # ------------------------------------------------------------------ seed
    def _seed(self) -> None:
        account = Account(
            account_id="acc_DEMO0000000000000000000000",
            display_name="Demo Owner",
            username="demo_owner",
            phone_masked="+7******0000",
            status=AccountSessionState.connected,
            created_at=_utcnow(),
            last_activity_at=_utcnow(),
        )
        self.accounts[account.account_id] = account

        channel = Channel(
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
        self.channels[channel.channel_id] = channel
        self.account_channels[account.account_id] = {channel.channel_id}

    # ------------------------------------------------------------------ utils
    def _within(self, container: dict[str, T] | OrderedDict[str, T], key: str) -> T:
        try:
            return container[key]
        except KeyError as exc:
            raise NotFoundError("Resource not found.") from exc

    def get_account(self, account_id: str) -> Account:
        with self._lock:
            return self._within(self.accounts, account_id)

    def list_accounts(self) -> list[Account]:
        with self._lock:
            return list(self.accounts.values())

    def add_account(self, account: Account) -> None:
        with self._lock:
            self.accounts[account.account_id] = account
            self.account_channels.setdefault(account.account_id, set())

    def remove_account(self, account_id: str) -> None:
        with self._lock:
            self.accounts.pop(account_id, None)
            self.account_channels.pop(account_id, None)

    # -------------- challenges
    def register_challenge(
        self,
        *,
        challenge_id: str,
        phone: str,
        device_name: str | None,
        callback_url: str | None,
        expires_at: datetime,
        backend_state: dict[str, Any] | None = None,
    ) -> LoginChallenge:
        with self._lock:
            challenge = LoginChallenge(
                challenge_id=challenge_id,
                phone=phone,
                device_name=device_name,
                callback_url=callback_url,
                expires_at=expires_at,
                backend_state=backend_state,
            )
            self.login_challenges[challenge_id] = challenge
            return challenge

    def pop_challenge(self, challenge_id: str) -> LoginChallenge:
        with self._lock:
            challenge = self.login_challenges.pop(challenge_id, None)
        if challenge is None:
            raise NotFoundError(
                "Login challenge not found.", code="challenge_not_found"
            )
        if _utcnow() > challenge.expires_at:
            raise ConflictError(
                "Login challenge has expired.", code="challenge_expired"
            )
        return challenge

    # -------------- channels
    def list_channels(
        self,
        account_id: str,
        *,
        only_writable: bool = True,
        role: ChannelRole | None = None,
    ) -> list[Channel]:
        with self._lock:
            self.get_account(account_id)
            ids = self.account_channels.get(account_id, set())
            channels = [self.channels[cid] for cid in ids if cid in self.channels]
            if only_writable:
                channels = [c for c in channels if c.permissions.can_publish]
            if role is not None:
                channels = [c for c in channels if c.role == role]
            return channels

    def get_channel(self, account_id: str, channel_id: str) -> Channel:
        with self._lock:
            self.get_account(account_id)
            ids = self.account_channels.get(account_id, set())
            if channel_id not in ids or channel_id not in self.channels:
                raise NotFoundError("Channel not found for this account.")
            return self.channels[channel_id]

    def upsert_channel(self, account_id: str, channel: Channel) -> Channel:
        with self._lock:
            self.get_account(account_id)
            self.channels[channel.channel_id] = channel
            self.account_channels.setdefault(account_id, set()).add(channel.channel_id)
            return channel

    def resolve_channel(self, account_id: str, link: str) -> Channel:
        with self._lock:
            self.get_account(account_id)
            normalized = link.lstrip("@").strip().lower()
            for channel_id in self.account_channels.get(account_id, set()):
                channel = self.channels.get(channel_id)
                if channel is None:
                    continue
                if channel.username and channel.username.lower() == normalized:
                    return channel
                if channel.public_link and normalized in channel.public_link.lower():
                    return channel
                if channel.channel_id == link:
                    return channel
            raise NotFoundError("Channel could not be resolved.", code="channel_not_resolved")

    # -------------- media
    def add_media(self, media: Media) -> Media:
        with self._lock:
            self.media[media.media_id] = media
            return media

    def get_media(self, media_id: str) -> Media:
        with self._lock:
            return self._within(self.media, media_id)

    # -------------- posts
    def list_posts(
        self,
        account_id: str,
        channel_id: str,
        *,
        since: datetime | None = None,
    ) -> list[PublishedPost]:
        with self._lock:
            self.get_channel(account_id, channel_id)
            posts = [
                post
                for (acc_id, ch_id, _), post in self.posts.items()
                if acc_id == account_id and ch_id == channel_id
            ]
            if since is not None:
                posts = [p for p in posts if p.published_at >= since]
            return posts

    def add_post(self, account_id: str, post: PublishedPost) -> PublishedPost:
        with self._lock:
            self.posts[(account_id, post.channel_id, post.message_id)] = post
            return post

    def get_post(
        self, account_id: str, channel_id: str, message_id: str
    ) -> PublishedPost:
        with self._lock:
            self.get_channel(account_id, channel_id)
            try:
                return self.posts[(account_id, channel_id, message_id)]
            except KeyError as exc:
                raise NotFoundError("Post not found.") from exc

    def replace_post(
        self, account_id: str, post: PublishedPost
    ) -> PublishedPost:
        with self._lock:
            self.posts[(account_id, post.channel_id, post.message_id)] = post
            return post

    def delete_post(self, account_id: str, channel_id: str, message_id: str) -> None:
        with self._lock:
            self.get_post(account_id, channel_id, message_id)
            self.posts.pop((account_id, channel_id, message_id), None)

    # -------------- scheduled posts
    def list_scheduled(
        self,
        account_id: str,
        *,
        channel_id: str | None = None,
        status: ScheduledPostStatus | None = None,
    ) -> list[ScheduledPost]:
        with self._lock:
            self.get_account(account_id)
            scheduled = [s for s in self.scheduled_posts.values() if s.account_id == account_id]
            if channel_id is not None:
                scheduled = [s for s in scheduled if s.channel_id == channel_id]
            if status is not None:
                scheduled = [s for s in scheduled if s.status == status]
            return scheduled

    def add_scheduled(self, schedule: ScheduledPost) -> ScheduledPost:
        with self._lock:
            self.scheduled_posts[schedule.schedule_id] = schedule
            return schedule

    def get_scheduled(self, account_id: str, schedule_id: str) -> ScheduledPost:
        with self._lock:
            self.get_account(account_id)
            schedule = self.scheduled_posts.get(schedule_id)
            if schedule is None or schedule.account_id != account_id:
                raise NotFoundError("Scheduled post not found.")
            return schedule

    def replace_scheduled(self, schedule: ScheduledPost) -> ScheduledPost:
        with self._lock:
            self.scheduled_posts[schedule.schedule_id] = schedule
            return schedule

    def delete_scheduled(self, account_id: str, schedule_id: str) -> None:
        with self._lock:
            self.get_scheduled(account_id, schedule_id)
            self.scheduled_posts.pop(schedule_id, None)

    # -------------- jobs
    def list_jobs(
        self,
        account_id: str,
        *,
        status: PublicationJobStatus | None = None,
        source: str | None = None,
    ) -> list[PublicationJob]:
        with self._lock:
            self.get_account(account_id)
            jobs = [j for j in self.jobs.values() if j.account_id == account_id]
            if status is not None:
                jobs = [j for j in jobs if j.status == status]
            if source is not None:
                jobs = [j for j in jobs if j.ready_post.source == source]
            return jobs

    def add_job(self, job: PublicationJob) -> PublicationJob:
        with self._lock:
            self.jobs[job.job_id] = job
            return job

    def get_job(self, account_id: str, job_id: str) -> PublicationJob:
        with self._lock:
            self.get_account(account_id)
            job = self.jobs.get(job_id)
            if job is None or job.account_id != account_id:
                raise NotFoundError("Publication job not found.")
            return job

    def replace_job(self, job: PublicationJob) -> PublicationJob:
        with self._lock:
            self.jobs[job.job_id] = job
            return job

    # -------------- webhooks
    def list_webhooks(self) -> list[WebhookSubscription]:
        with self._lock:
            return list(self.webhooks.values())

    def add_webhook(self, subscription: WebhookSubscription) -> WebhookSubscription:
        with self._lock:
            self.webhooks[subscription.subscription_id] = subscription
            return subscription

    def get_webhook(self, subscription_id: str) -> WebhookSubscription:
        with self._lock:
            return self._within(self.webhooks, subscription_id)

    def remove_webhook(self, subscription_id: str) -> None:
        with self._lock:
            if subscription_id not in self.webhooks:
                raise NotFoundError("Webhook subscription not found.")
            self.webhooks.pop(subscription_id, None)

    # -------------- idempotency
    def idempotent(
        self,
        operation: str,
        key: str | None,
        producer: Callable[[], T],
    ) -> T:
        if not key:
            return producer()
        with self._lock:
            cached = self.idempotency.get((operation, key))
        if cached is not None:
            return cached  # type: ignore[return-value]
        value = producer()
        with self._lock:
            self.idempotency[(operation, key)] = value
        return value

    def idempotency_get(self, operation: str, key: str | None) -> Any | None:
        if not key:
            return None
        with self._lock:
            return self.idempotency.get((operation, key))

    def idempotency_set(self, operation: str, key: str | None, value: Any) -> None:
        if not key:
            return
        with self._lock:
            self.idempotency[(operation, key)] = value


# ----------------------------------------------------------------------------
# Helpers shared between routers (kept here so they have access to enums).
# ----------------------------------------------------------------------------


def materialize_published_post(
    *,
    channel_id: str,
    request: PublishPostRequest,
    options: PublishOptions | None,
    media: Iterable[Media] = (),
    message_id: str | None = None,
    permalink: str | None = None,
) -> PublishedPost:
    """Build a published-post envelope from a publish request.

    If ``message_id`` is omitted (e.g. dry-run path) a synthetic one is
    generated; the real backends are expected to pass the upstream id back.
    """
    return PublishedPost(
        message_id=message_id or new_message_id(),
        channel_id=channel_id,
        external_id=request.external_id,
        status=PublishedPostStatus.published,
        text=request.text,
        format=request.format,
        permalink=permalink,
        media=list(media),
        pinned=bool(options and options.pin_after_publish),
        published_at=_utcnow(),
        edited_at=None,
        raw_response=None,
    )


def materialize_media_from_refs(
    storage: Storage,
    refs: Iterable[MediaRef],
) -> list[Media]:
    """Resolve media refs into stored Media records, importing URL refs as needed."""
    resolved: list[Media] = []
    for ref in refs:
        if ref.media_id is not None:
            resolved.append(storage.get_media(ref.media_id))
            continue
        if ref.url:
            media = Media(
                media_id=new_id("med"),
                type=_coerce_media_type(ref.type),
                status=MediaStatus.ready,
                url=ref.url,
                created_at=_utcnow(),
            )
            storage.add_media(media)
            resolved.append(media)
    return resolved


def _coerce_media_type(value: MediaType | str) -> MediaType:
    if isinstance(value, MediaType):
        return value
    return MediaType(value)


def empty_keyboard() -> InlineKeyboard:
    return InlineKeyboard(rows=[])


def text_format_default() -> TextFormat:
    return TextFormat.plain


def now_utc() -> datetime:
    return _utcnow()
