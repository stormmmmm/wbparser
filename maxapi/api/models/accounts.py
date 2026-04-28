"""Account session and login models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from api.models.common import APIModel, PageMeta


class AccountSessionState(str, Enum):
    connected = "connected"
    login_required = "login_required"
    challenge_required = "challenge_required"
    rate_limited = "rate_limited"
    disabled = "disabled"


class Account(APIModel):
    account_id: str
    max_user_id: str | None = None
    phone_masked: str | None = None
    username: str | None = None
    display_name: str
    status: AccountSessionState
    last_activity_at: datetime | None = None
    created_at: datetime


class AccountListResponse(PageMeta):
    items: list[Account] = Field(default_factory=list)


class AccountStatus(APIModel):
    account_id: str
    status: AccountSessionState
    can_publish: bool
    reason: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.utcnow())


class StartLoginRequest(APIModel):
    phone: str = Field(min_length=4, max_length=32)
    device_name: str = "wb-channel-poster"
    callback_url: str | None = None


class LoginDelivery(str, Enum):
    sms = "sms"
    push = "push"
    in_app = "in_app"


class StartLoginResponse(APIModel):
    challenge_id: str
    expires_at: datetime
    delivery: LoginDelivery
    masked_destination: str | None = None


class VerifyLoginRequest(APIModel):
    challenge_id: str
    code: str = Field(min_length=4, max_length=12)
    two_factor_password: str | None = None


class VerifyLoginResponse(APIModel):
    account: Account
