"""/v1/webhooks/subscriptions/* endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, status

from api.deps import AuthDep, CursorDep, LimitDep, StorageDep
from api.errors import ConflictError
from api.ids import new_id
from api.models.webhooks import (
    CreateWebhookSubscriptionRequest,
    WebhookSubscription,
    WebhookSubscriptionListResponse,
)
from api.pagination import paginate

router = APIRouter(prefix="/v1/webhooks/subscriptions", tags=["webhooks"])


@router.get(
    "",
    summary="List webhook subscriptions",
    response_model=WebhookSubscriptionListResponse,
    operation_id="listWebhookSubscriptions",
)
def list_webhook_subscriptions(
    storage: StorageDep,
    _auth: AuthDep,
    limit: LimitDep,
    cursor: CursorDep,
) -> WebhookSubscriptionListResponse:
    items, next_cursor = paginate(storage.list_webhooks(), cursor, limit)
    return WebhookSubscriptionListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "",
    summary="Create webhook subscription",
    response_model=WebhookSubscription,
    status_code=status.HTTP_201_CREATED,
    operation_id="createWebhookSubscription",
)
def create_webhook_subscription(
    payload: CreateWebhookSubscriptionRequest,
    storage: StorageDep,
    _auth: AuthDep,
) -> WebhookSubscription:
    for existing in storage.list_webhooks():
        if existing.url == payload.url and set(existing.events) == set(payload.events):
            raise ConflictError(
                "An equivalent webhook subscription already exists.",
                code="duplicate_subscription",
            )
    subscription = WebhookSubscription(
        subscription_id=new_id("whs"),
        url=payload.url,
        events=payload.events,
        created_at=datetime.now(timezone.utc),
    )
    return storage.add_webhook(subscription)


@router.delete(
    "/{subscription_id}",
    summary="Delete webhook subscription",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteWebhookSubscription",
)
def delete_webhook_subscription(
    subscription_id: str, storage: StorageDep, _auth: AuthDep
) -> None:
    storage.remove_webhook(subscription_id)


__all__ = ["router"]
