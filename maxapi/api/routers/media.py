"""/v1/accounts/{accountId}/media* endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from api.backends import UpstreamMedia
from api.config import Settings, get_settings
from api.deps import AuthDep, BackendDep, IdempotencyKeyDep, StorageDep
from api.errors import BadRequestError, PayloadTooLargeError
from api.models.media import Media, MediaImportRequest, MediaStatus, MediaType

router = APIRouter(prefix="/v1/accounts/{account_id}/media", tags=["media"])


def _to_media(upstream: UpstreamMedia, *, fallback_type: MediaType) -> Media:
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


@router.post(
    "",
    summary="Upload media file",
    response_model=Media,
    status_code=status.HTTP_201_CREATED,
    operation_id="uploadMedia",
)
async def upload_media(
    account_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    idempotency_key: IdempotencyKeyDep,
    type: Annotated[MediaType, Form(description="Media kind.")],
    file: Annotated[UploadFile, File(description="Binary file payload.")],
    filename: Annotated[str | None, Form()] = None,
    caption: Annotated[str | None, Form(max_length=1024)] = None,
    settings: Settings = Depends(get_settings),
) -> Media:
    storage.get_account(account_id)
    body = await file.read()
    if len(body) > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"Uploaded media exceeds {settings.max_upload_bytes} bytes.",
        )

    operation = f"uploadMedia:{account_id}"
    cached = storage.idempotency_get(operation, idempotency_key)
    if cached is not None:
        return cached

    upstream = await backend.upload_media(
        account_id=account_id,
        media_type=type,
        content=body,
        filename=filename or file.filename,
        mime_type=file.content_type,
        caption=caption,
    )
    media = _to_media(upstream, fallback_type=type)
    storage.add_media(media)
    storage.idempotency_set(operation, idempotency_key, media)
    return media


@router.post(
    "/import",
    summary="Import media by URL",
    response_model=Media,
    status_code=status.HTTP_201_CREATED,
    operation_id="importMedia",
)
async def import_media(
    account_id: str,
    payload: MediaImportRequest,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
    idempotency_key: IdempotencyKeyDep,
) -> Media:
    storage.get_account(account_id)
    if not payload.url.startswith(("http://", "https://")):
        raise BadRequestError("Media URL must be HTTP/HTTPS.", code="invalid_media_url")

    operation = f"importMedia:{account_id}"
    cached = storage.idempotency_get(operation, idempotency_key)
    if cached is not None:
        return cached

    upstream = await backend.import_media(
        account_id=account_id,
        media_type=payload.type,
        url=payload.url,
        filename=payload.filename,
        caption=None,
        source_post_id=payload.source_post_id,
    )
    media = _to_media(upstream, fallback_type=payload.type)
    storage.add_media(media)
    storage.idempotency_set(operation, idempotency_key, media)
    return media


__all__ = ["router"]
