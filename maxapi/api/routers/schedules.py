"""/v1/accounts/{accountId}/scheduled-posts/* endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, status

from api.backends import media_to_upstream
from api.deps import AuthDep, BackendDep, CursorDep, IdempotencyKeyDep, LimitDep, StorageDep
from api.errors import ConflictError, ValidationFailedError
from api.ids import new_id
from api.models.common import ValidationIssue
from api.models.posts import PublishedPost
from api.models.schedules import (
    ScheduledPost,
    ScheduledPostListResponse,
    ScheduledPostStatus,
    SchedulePostRequest,
    UpdateScheduledPostRequest,
)
from api.pagination import paginate
from api.storage import (
    materialize_media_from_refs,
    materialize_published_post,
    now_utc,
)

router = APIRouter(
    prefix="/v1/accounts/{account_id}/scheduled-posts",
    tags=["schedules"],
)


@router.get(
    "",
    summary="List scheduled posts",
    response_model=ScheduledPostListResponse,
    operation_id="listScheduledPosts",
)
def list_scheduled_posts(
    account_id: str,
    storage: StorageDep,
    _auth: AuthDep,
    limit: LimitDep,
    cursor: CursorDep,
    channel_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[
        ScheduledPostStatus | None, Query(alias="status")
    ] = None,
) -> ScheduledPostListResponse:
    scheduled = storage.list_scheduled(
        account_id, channel_id=channel_id, status=status_filter
    )
    items, next_cursor = paginate(scheduled, cursor, limit)
    return ScheduledPostListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "",
    summary="Schedule post",
    response_model=ScheduledPost,
    status_code=status.HTTP_201_CREATED,
    operation_id="schedulePost",
)
def schedule_post(
    account_id: str,
    payload: SchedulePostRequest,
    storage: StorageDep,
    _auth: AuthDep,
    idempotency_key: IdempotencyKeyDep,
) -> ScheduledPost:
    storage.get_channel(account_id, payload.channel_id)
    if payload.publish_at <= now_utc():
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

    def _build() -> ScheduledPost:
        return ScheduledPost(
            schedule_id=new_id("sch"),
            account_id=account_id,
            channel_id=payload.channel_id,
            status=ScheduledPostStatus.scheduled,
            publish_at=payload.publish_at,
            post=payload.post,
            published_post=None,
            created_at=now_utc(),
            updated_at=None,
        )

    schedule = storage.idempotent(
        f"schedulePost:{account_id}", idempotency_key, _build
    )
    return storage.add_scheduled(schedule)


@router.get(
    "/{schedule_id}",
    summary="Get scheduled post",
    response_model=ScheduledPost,
    operation_id="getScheduledPost",
)
def get_scheduled_post(
    account_id: str, schedule_id: str, storage: StorageDep, _auth: AuthDep
) -> ScheduledPost:
    return storage.get_scheduled(account_id, schedule_id)


@router.patch(
    "/{schedule_id}",
    summary="Update scheduled post",
    response_model=ScheduledPost,
    operation_id="updateScheduledPost",
)
def update_scheduled_post(
    account_id: str,
    schedule_id: str,
    payload: UpdateScheduledPostRequest,
    storage: StorageDep,
    _auth: AuthDep,
) -> ScheduledPost:
    schedule = storage.get_scheduled(account_id, schedule_id)
    if schedule.status not in (ScheduledPostStatus.scheduled,):
        raise ConflictError(
            "Scheduled post can only be modified while in 'scheduled' state.",
            code="schedule_not_modifiable",
        )
    update: dict = {"updated_at": datetime.now(timezone.utc)}
    if payload.publish_at is not None:
        if payload.publish_at <= now_utc():
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
        update["publish_at"] = payload.publish_at
    if payload.post is not None:
        update["post"] = payload.post
    new_schedule = schedule.model_copy(update=update)
    return storage.replace_scheduled(new_schedule)


@router.delete(
    "/{schedule_id}",
    summary="Cancel scheduled post",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="cancelScheduledPost",
)
def cancel_scheduled_post(
    account_id: str, schedule_id: str, storage: StorageDep, _auth: AuthDep
) -> None:
    schedule = storage.get_scheduled(account_id, schedule_id)
    if schedule.status == ScheduledPostStatus.published:
        raise ConflictError("Already published scheduled posts cannot be cancelled.")
    storage.delete_scheduled(account_id, schedule_id)


@router.post(
    "/{schedule_id}/publish-now",
    summary="Publish scheduled post immediately",
    response_model=PublishedPost,
    operation_id="publishScheduledPostNow",
    status_code=status.HTTP_201_CREATED,
)
async def publish_scheduled_post_now(
    account_id: str,
    schedule_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    idempotency_key: IdempotencyKeyDep,
) -> PublishedPost:
    schedule = storage.get_scheduled(account_id, schedule_id)
    if schedule.status not in (
        ScheduledPostStatus.scheduled,
        ScheduledPostStatus.failed,
    ):
        raise ConflictError("Schedule is not eligible for immediate publishing.")
    storage.get_channel(account_id, schedule.channel_id)

    operation = f"publishScheduledPostNow:{schedule_id}"
    cached = storage.idempotency_get(operation, idempotency_key)
    if cached is not None:
        return cached

    media = materialize_media_from_refs(storage, schedule.post.media)
    upstream_media = media_to_upstream(media)
    upstream_msg = await backend.publish_message(
        account_id=account_id,
        channel_id=schedule.channel_id,
        request=schedule.post,
        options=schedule.post.options,
        media=upstream_media,
    )
    published = materialize_published_post(
        channel_id=schedule.channel_id,
        request=schedule.post,
        options=schedule.post.options,
        media=media,
        message_id=upstream_msg.message_id,
        permalink=upstream_msg.permalink,
    )
    storage.idempotency_set(operation, idempotency_key, published)
    storage.add_post(account_id, published)
    storage.replace_scheduled(
        schedule.model_copy(
            update={
                "status": ScheduledPostStatus.published,
                "published_post": published,
                "updated_at": now_utc(),
            }
        )
    )
    return published


__all__ = ["router"]
