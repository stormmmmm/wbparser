"""/v1/accounts/* endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, status

from api.deps import AuthDep, BackendDep, CursorDep, LimitDep, StorageDep
from api.ids import new_id
from api.models.accounts import (
    Account,
    AccountListResponse,
    AccountSessionState,
    AccountStatus,
    LoginDelivery,
    StartLoginRequest,
    StartLoginResponse,
    VerifyLoginRequest,
    VerifyLoginResponse,
)
from api.pagination import paginate

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.get(
    "",
    summary="List connected MAX accounts",
    response_model=AccountListResponse,
    operation_id="listAccounts",
)
def list_accounts(
    storage: StorageDep,
    _auth: AuthDep,
    limit: LimitDep,
    cursor: CursorDep,
) -> AccountListResponse:
    items, next_cursor = paginate(storage.list_accounts(), cursor, limit)
    return AccountListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "/login/start",
    summary="Start MAX user login",
    response_model=StartLoginResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="startAccountLogin",
)
async def start_account_login(
    payload: StartLoginRequest,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
) -> StartLoginResponse:
    data = await backend.start_login(
        phone=payload.phone,
        device_name=payload.device_name or "MAX user",
        callback_url=payload.callback_url,
    )
    storage.register_challenge(
        challenge_id=data.challenge_id,
        phone=payload.phone,
        device_name=payload.device_name,
        callback_url=payload.callback_url,
        expires_at=data.expires_at,
        backend_state=data.backend_state,
    )
    return StartLoginResponse(
        challenge_id=data.challenge_id,
        expires_at=data.expires_at,
        delivery=LoginDelivery(data.delivery) if data.delivery in {d.value for d in LoginDelivery} else LoginDelivery.sms,
        masked_destination=data.masked_destination or _mask_phone(payload.phone),
    )


@router.post(
    "/login/verify",
    summary="Verify MAX login challenge",
    response_model=VerifyLoginResponse,
    operation_id="verifyAccountLogin",
)
async def verify_account_login(
    payload: VerifyLoginRequest,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
) -> VerifyLoginResponse:
    challenge = storage.pop_challenge(payload.challenge_id)
    profile = await backend.verify_login(
        challenge_state=challenge.backend_state,
        code=payload.code,
        two_factor_password=payload.two_factor_password,
    )
    now = datetime.now(timezone.utc)
    account_id = new_id("acc")
    account = Account(
        account_id=account_id,
        max_user_id=profile.get("max_user_id"),
        phone_masked=_mask_phone(challenge.phone),
        username=profile.get("username"),
        display_name=profile.get("display_name") or challenge.device_name or "MAX user",
        status=AccountSessionState.connected,
        last_activity_at=now,
        created_at=now,
    )
    storage.add_account(account)
    pending = profile.get("_pymax_pending")
    if pending is not None and hasattr(backend, "attach_account"):
        await backend.attach_account(account_id=account_id, pending=pending)
    return VerifyLoginResponse(account=account)


@router.get(
    "/{account_id}",
    summary="Get account",
    response_model=Account,
    operation_id="getAccount",
)
def get_account(account_id: str, storage: StorageDep, _auth: AuthDep) -> Account:
    return storage.get_account(account_id)


@router.get(
    "/{account_id}/status",
    summary="Get account session status",
    response_model=AccountStatus,
    operation_id="getAccountStatus",
)
def get_account_status(
    account_id: str, storage: StorageDep, _auth: AuthDep
) -> AccountStatus:
    account = storage.get_account(account_id)
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


@router.post(
    "/{account_id}/logout",
    summary="Disconnect account",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="logoutAccount",
)
async def logout_account(
    account_id: str,
    storage: StorageDep,
    backend: BackendDep,
    _auth: AuthDep,
) -> None:
    storage.get_account(account_id)
    await backend.logout(account_id=account_id)
    storage.remove_account(account_id)


def _mask_phone(phone: str) -> str | None:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 4:
        return None
    masked_body = "*" * max(len(digits) - 5, 1)
    return f"+{digits[0]}{masked_body}{digits[-4:]}"


__all__ = ["router"]
