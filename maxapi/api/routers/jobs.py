"""/v1/accounts/{accountId}/publication-jobs/* endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, status

from api.backends import media_to_upstream
from api.deps import AuthDep, BackendDep, CursorDep, IdempotencyKeyDep, LimitDep, StorageDep
from api.errors import ConflictError, ForbiddenError, NotFoundError
from api.ids import new_id
from api.models.jobs import (
    CreatePublicationJobRequest,
    PublicationJob,
    PublicationJobListResponse,
    PublicationJobMode,
    PublicationJobStatus,
    ReadyPost,
)
from api.models.media import MediaType
from api.models.posts import (
    InlineButton,
    InlineButtonType,
    InlineKeyboard,
    MediaRef,
    PublishOptions,
    PublishPostRequest,
    TextFormat,
)
from api.models.schedules import ScheduledPost, ScheduledPostStatus
from api.pagination import paginate
from api.routers.channels import _to_channel
from api.storage import (
    Storage,
    materialize_media_from_refs,
    materialize_published_post,
    now_utc,
)

router = APIRouter(
    prefix="/v1/accounts/{account_id}/publication-jobs",
    tags=["jobs"],
)


def _ready_post_to_publish_request(ready_post: ReadyPost) -> PublishPostRequest:
    fmt = TextFormat.plain
    if ready_post.parse_mode is not None:
        fmt = TextFormat(ready_post.parse_mode.value)
    media_refs = [
        MediaRef(
            url=item.url,
            type=_ready_media_type(item.type),
            caption=item.caption,
            position=item.position,
        )
        for item in ready_post.media
    ]
    keyboard: InlineKeyboard | None = None
    if ready_post.buttons:
        keyboard = InlineKeyboard(
            rows=[
                [
                    InlineButton(
                        type=InlineButtonType.link,
                        text=btn.text,
                        url=btn.url,
                    )
                    for btn in ready_post.buttons
                ]
            ]
        )
    return PublishPostRequest(
        external_id=ready_post.post_id,
        title=ready_post.title,
        text=ready_post.text,
        format=fmt,
        media=media_refs,
        inline_keyboard=keyboard,
        options=None,
        source=ready_post.source,
        metadata={
            "post_type": ready_post.post_type.value,
            "fresh_until": ready_post.fresh_until.isoformat(),
            "version": ready_post.version,
        },
    )


async def _ensure_writable_channel(
    storage: Storage,
    backend,
    account_id: str,
    channel_id: str,
) -> None:
    try:
        channel = storage.get_channel(account_id, channel_id)
    except Exception:
        storage.get_account(account_id)
        upstream = await backend.resolve_channel(account_id=account_id, link=channel_id)
        if upstream is None:
            for candidate in await backend.list_channels(account_id=account_id):
                if str(candidate.channel_id) == str(channel_id):
                    upstream = candidate
                    break
        if upstream is None:
            raise NotFoundError(
                "Channel not found for this account.", code="channel_not_found"
            ) from None
        channel = storage.upsert_channel(account_id, _to_channel(upstream))
    if not channel.permissions.can_publish:
        raise ForbiddenError("Account cannot publish to this channel.")


def _ready_media_type(value) -> MediaType:
    mapping = {
        "photo": MediaType.image,
        "image": MediaType.image,
        "video": MediaType.video,
        "document": MediaType.document,
    }
    key = value.value if hasattr(value, "value") else str(value)
    return mapping.get(key, MediaType.image)


@router.get(
    "",
    summary="List publication jobs",
    response_model=PublicationJobListResponse,
    operation_id="listPublicationJobs",
)
def list_publication_jobs(
    account_id: str,
    storage: StorageDep,
    _auth: AuthDep,
    limit: LimitDep,
    cursor: CursorDep,
    status_filter: Annotated[
        PublicationJobStatus | None, Query(alias="status")
    ] = None,
    source: Annotated[str | None, Query()] = None,
) -> PublicationJobListResponse:
    jobs = storage.list_jobs(account_id, status=status_filter, source=source)
    items, next_cursor = paginate(jobs, cursor, limit)
    return PublicationJobListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "",
    summary="Create publication job from ReadyPost",
    response_model=PublicationJob,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createPublicationJob",
)
async def create_publication_job(
    account_id: str,
    payload: CreatePublicationJobRequest,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    idempotency_key: IdempotencyKeyDep,
) -> PublicationJob:
    await _ensure_writable_channel(storage, backend, account_id, payload.channel_id)
    publish_request = _ready_post_to_publish_request(payload.ready_post)
    options = payload.options or PublishOptions()

    operation = f"createPublicationJob:{account_id}"
    cached = storage.idempotency_get(operation, idempotency_key)
    if cached is not None:
        return cached

    now = now_utc()
    mode = _resolve_mode(payload, options)
    if mode == PublicationJobMode.dry_run:
        job = PublicationJob(
            job_id=new_id("job"),
            account_id=account_id,
            channel_id=payload.channel_id,
            status=PublicationJobStatus.published,
            ready_post=payload.ready_post,
            attempts=1,
            created_at=now,
            updated_at=now,
        )
    elif mode == PublicationJobMode.schedule:
        publish_at = payload.publish_at or payload.ready_post.planned_at
        if publish_at is None or publish_at <= now:
            raise ConflictError(
                "publish_at is required and must be in the future for scheduled jobs.",
                code="invalid_schedule_time",
            )
        scheduled = ScheduledPost(
            schedule_id=new_id("sch"),
            account_id=account_id,
            channel_id=payload.channel_id,
            status=ScheduledPostStatus.scheduled,
            publish_at=publish_at,
            post=publish_request.model_copy(update={"options": options}),
            created_at=now,
        )
        storage.add_scheduled(scheduled)
        job = PublicationJob(
            job_id=new_id("job"),
            account_id=account_id,
            channel_id=payload.channel_id,
            status=PublicationJobStatus.scheduled,
            ready_post=payload.ready_post,
            scheduled_post=scheduled,
            attempts=1,
            created_at=now,
            updated_at=now,
        )
    else:
        # publish_now / auto: actually go through the upstream backend.
        media = materialize_media_from_refs(storage, publish_request.media)
        upstream_media = media_to_upstream(media)
        upstream_msg = await backend.publish_message(
            account_id=account_id,
            channel_id=payload.channel_id,
            request=publish_request,
            options=options,
            media=upstream_media,
        )
        published = materialize_published_post(
            channel_id=payload.channel_id,
            request=publish_request,
            options=options,
            media=media,
            message_id=upstream_msg.message_id,
            permalink=upstream_msg.permalink,
        )
        storage.add_post(account_id, published)
        job = PublicationJob(
            job_id=new_id("job"),
            account_id=account_id,
            channel_id=payload.channel_id,
            status=PublicationJobStatus.published,
            ready_post=payload.ready_post,
            published_post=published,
            attempts=1,
            created_at=now,
            updated_at=now,
        )

    storage.idempotency_set(operation, idempotency_key, job)
    return storage.add_job(job)


def _resolve_mode(
    payload: CreatePublicationJobRequest, options: PublishOptions
) -> PublicationJobMode:
    if options.dry_run:
        return PublicationJobMode.dry_run
    if payload.mode != PublicationJobMode.auto:
        return payload.mode
    if payload.publish_at is not None or payload.ready_post.planned_at is not None:
        return PublicationJobMode.schedule
    return PublicationJobMode.publish_now


@router.get(
    "/{job_id}",
    summary="Get publication job",
    response_model=PublicationJob,
    operation_id="getPublicationJob",
)
def get_publication_job(
    account_id: str, job_id: str, storage: StorageDep, _auth: AuthDep
) -> PublicationJob:
    return storage.get_job(account_id, job_id)


@router.post(
    "/{job_id}/cancel",
    summary="Cancel queued publication job",
    response_model=PublicationJob,
    operation_id="cancelPublicationJob",
)
def cancel_publication_job(
    account_id: str, job_id: str, storage: StorageDep, _auth: AuthDep
) -> PublicationJob:
    job = storage.get_job(account_id, job_id)
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
    return storage.replace_job(cancelled)


__all__ = ["router"]
