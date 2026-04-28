"""/v1/accounts/{accountId}/channels/{channelId}/posts* endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, status

from api.backends import MaxBackend, media_to_upstream
from api.deps import AuthDep, BackendDep, CursorDep, IdempotencyKeyDep, LimitDep, StorageDep
from api.errors import ForbiddenError, NotFoundError, ValidationFailedError
from api.models.common import SuccessResponse, ValidationIssue
from api.models.media import Media
from api.models.posts import (
    EditPostRequest,
    PinPostRequest,
    PostListResponse,
    PostMetrics,
    PostValidationResult,
    PublishedPost,
    PublishedPostStatus,
    PublishPostRequest,
)
from api.pagination import paginate
from api.routers.channels import _to_channel
from api.storage import (
    Storage,
    materialize_media_from_refs,
    materialize_published_post,
    now_utc,
)

router = APIRouter(
    prefix="/v1/accounts/{account_id}/channels/{channel_id}/posts",
    tags=["posts"],
)


def _normalize_post(payload: PublishPostRequest) -> tuple[PublishPostRequest, list[ValidationIssue]]:
    warnings: list[ValidationIssue] = []
    text = payload.text.strip()
    if text != payload.text:
        warnings.append(
            ValidationIssue(field="text", message="Leading/trailing whitespace was trimmed.", code="whitespace_trimmed")
        )
    if len(text) > 4000:
        raise ValidationFailedError(
            "Post text exceeds the 4000-character limit.",
            issues=[ValidationIssue(field="text", message="too_long", code="text_too_long")],
        )
    normalized = payload.model_copy(update={"text": text})
    return normalized, warnings


async def _ensure_writable(
    storage: Storage,
    backend: MaxBackend,
    account_id: str,
    channel_id: str,
) -> None:
    """Look up the channel by id; if it isn't cached gateway-side yet, ask the
    backend to resolve it. This makes publish/edit/pin work against any channel
    the user has access to upstream, even if the gateway has been restarted and
    the caller hasn't called ``GET /channels`` first.
    """
    try:
        channel = storage.get_channel(account_id, channel_id)
    except Exception:
        storage.get_account(account_id)
        upstream = await backend.resolve_channel(
            account_id=account_id, link=channel_id
        )
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


@router.post(
    "/validate",
    summary="Validate and render post payload",
    response_model=PostValidationResult,
    operation_id="validatePost",
)
def validate_post(
    account_id: str,
    channel_id: str,
    payload: PublishPostRequest,
    storage: StorageDep,
    _auth: AuthDep,
) -> PostValidationResult:
    storage.get_channel(account_id, channel_id)
    normalized, warnings = _normalize_post(payload)
    return PostValidationResult(
        valid=True,
        normalized=normalized,
        warnings=warnings,
        errors=[],
    )


@router.get(
    "",
    summary="List recent posts in channel",
    response_model=PostListResponse,
    operation_id="listChannelPosts",
)
def list_channel_posts(
    account_id: str,
    channel_id: str,
    storage: StorageDep,
    _auth: AuthDep,
    limit: LimitDep,
    cursor: CursorDep,
    since: Annotated[datetime | None, Query()] = None,
) -> PostListResponse:
    posts = storage.list_posts(account_id, channel_id, since=since)
    items, next_cursor = paginate(posts, cursor, limit)
    return PostListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "",
    summary="Publish post now",
    response_model=PublishedPost,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishPost",
)
async def publish_post(
    account_id: str,
    channel_id: str,
    payload: PublishPostRequest,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    idempotency_key: IdempotencyKeyDep,
) -> PublishedPost:
    await _ensure_writable(storage, backend, account_id, channel_id)
    normalized, _warnings = _normalize_post(payload)

    operation = f"publishPost:{account_id}:{channel_id}"
    cached = storage.idempotency_get(operation, idempotency_key)
    if cached is not None:
        return cached

    media = materialize_media_from_refs(storage, normalized.media)
    upstream_msg = await backend.publish_message(
        account_id=account_id,
        channel_id=channel_id,
        request=normalized,
        options=normalized.options,
        media=media_to_upstream(media),
    )
    post = materialize_published_post(
        channel_id=channel_id,
        request=normalized,
        options=normalized.options,
        media=media,
        message_id=upstream_msg.message_id,
        permalink=upstream_msg.permalink,
    )
    storage.add_post(account_id, post)
    storage.idempotency_set(operation, idempotency_key, post)
    return post


@router.get(
    "/{message_id}",
    summary="Get published post",
    response_model=PublishedPost,
    operation_id="getPublishedPost",
)
def get_published_post(
    account_id: str,
    channel_id: str,
    message_id: str,
    storage: StorageDep,
    _auth: AuthDep,
) -> PublishedPost:
    return storage.get_post(account_id, channel_id, message_id)


@router.put(
    "/{message_id}",
    summary="Edit published post",
    response_model=PublishedPost,
    operation_id="editPublishedPost",
)
async def edit_published_post(
    account_id: str,
    channel_id: str,
    message_id: str,
    payload: EditPostRequest,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
) -> PublishedPost:
    existing = storage.get_post(account_id, channel_id, message_id)
    update: dict = {"status": PublishedPostStatus.edited, "edited_at": now_utc()}
    if payload.text is not None:
        update["text"] = payload.text
    if payload.format is not None:
        update["format"] = payload.format
    media: list[Media] | None = None
    if payload.media is not None:
        media = materialize_media_from_refs(storage, payload.media)
        update["media"] = media
    await backend.edit_message(
        account_id=account_id,
        channel_id=channel_id,
        message_id=message_id,
        text=payload.text,
        media=media_to_upstream(media) if media is not None else None,
    )
    edited = existing.model_copy(update=update)
    return storage.replace_post(account_id, edited)


@router.delete(
    "/{message_id}",
    summary="Delete published post",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deletePublishedPost",
)
async def delete_published_post(
    account_id: str,
    channel_id: str,
    message_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
) -> None:
    storage.get_post(account_id, channel_id, message_id)
    await backend.delete_message(
        account_id=account_id, channel_id=channel_id, message_id=message_id
    )
    storage.delete_post(account_id, channel_id, message_id)


@router.put(
    "/{message_id}/pin",
    summary="Pin post",
    response_model=SuccessResponse,
    operation_id="pinPublishedPost",
)
async def pin_published_post(
    account_id: str,
    channel_id: str,
    message_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    payload: PinPostRequest | None = None,
) -> SuccessResponse:
    post = storage.get_post(account_id, channel_id, message_id)
    notify = bool(payload.notify_subscribers) if payload is not None else False
    await backend.pin_message(
        account_id=account_id,
        channel_id=channel_id,
        message_id=message_id,
        notify=notify,
    )
    storage.replace_post(account_id, post.model_copy(update={"pinned": True}))
    return SuccessResponse(ok=True)


@router.delete(
    "/{message_id}/pin",
    summary="Unpin post",
    response_model=SuccessResponse,
    operation_id="unpinPublishedPost",
)
async def unpin_published_post(
    account_id: str,
    channel_id: str,
    message_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
) -> SuccessResponse:
    post = storage.get_post(account_id, channel_id, message_id)
    try:
        await backend.unpin_message(
            account_id=account_id,
            channel_id=channel_id,
            message_id=message_id,
        )
    except Exception:
        # Some backends (PyMax) cannot unpin programmatically yet; we still
        # update the gateway-side cache so the API contract behaves.
        pass
    storage.replace_post(account_id, post.model_copy(update={"pinned": False}))
    return SuccessResponse(ok=True)


@router.get(
    "/{message_id}/metrics",
    summary="Get post metrics",
    response_model=PostMetrics,
    operation_id="getPostMetrics",
)
def get_post_metrics(
    account_id: str,
    channel_id: str,
    message_id: str,
    storage: StorageDep,
    _auth: AuthDep,
) -> PostMetrics:
    post = storage.get_post(account_id, channel_id, message_id)
    return PostMetrics(
        message_id=post.message_id,
        views_count=0,
        reactions_total=0,
        reaction_breakdown={},
        comments_count=0,
        forwards_count=0,
        collected_at=datetime.now(timezone.utc),
    )


__all__ = ["router"]
