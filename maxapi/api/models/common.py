"""Shared Pydantic models used across all routers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    """Base model with permissive serialization defaults."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class PageMeta(APIModel):
    next_cursor: str | None = Field(default=None)


class SuccessResponse(APIModel):
    ok: bool = True


class ValidationIssue(APIModel):
    field: str
    message: str
    code: str | None = None


class ErrorResponse(APIModel):
    code: str
    message: str
    request_id: str | None = None
    retryable: bool = False


class ValidationErrorResponse(ErrorResponse):
    errors: list[ValidationIssue] = Field(default_factory=list)
