"""HTTP error handling and exception types for the MAX userbot gateway."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from api.models.common import ErrorResponse, ValidationErrorResponse, ValidationIssue


class APIError(Exception):
    """Base API error mapped to a JSON ErrorResponse payload."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        self.request_id = request_id
        self.headers = headers or {}

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            code=self.code,
            message=self.message,
            request_id=self.request_id,
            retryable=self.retryable,
        )


class BadRequestError(APIError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"


class UnauthorizedError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class NotFoundError(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class PayloadTooLargeError(APIError):
    status_code = 413
    code = "payload_too_large"


class ValidationFailedError(APIError):
    status_code = 422
    code = "validation_failed"

    def __init__(
        self,
        message: str,
        *,
        issues: list[ValidationIssue] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.issues: list[ValidationIssue] = list(issues or [])

    def to_response(self) -> ValidationErrorResponse:  # type: ignore[override]
        return ValidationErrorResponse(
            code=self.code,
            message=self.message,
            request_id=self.request_id,
            retryable=self.retryable,
            errors=self.issues,
        )


class TooManyRequestsError(APIError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    retryable = True

    def __init__(self, message: str, *, retry_after: int = 1, **kwargs: Any) -> None:
        headers = {"Retry-After": str(max(int(retry_after), 1))}
        kwargs.setdefault("headers", headers)
        super().__init__(message, **kwargs)


class ServiceUnavailableError(APIError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    retryable = True


def install_error_handlers(app: FastAPI) -> None:
    """Register exception handlers that produce ErrorResponse payloads."""

    @app.exception_handler(APIError)
    async def _api_error_handler(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response().model_dump(mode="json"),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        issues = [
            ValidationIssue(
                field=".".join(str(part) for part in err.get("loc", ()) if part != "body"),
                message=str(err.get("msg", "invalid value")),
                code=str(err.get("type")) if err.get("type") else None,
            )
            for err in exc.errors()
        ]
        payload = ValidationErrorResponse(
            code="validation_failed",
            message="Request payload failed validation.",
            errors=issues,
        )
        return JSONResponse(
            status_code=422,
            content=payload.model_dump(mode="json"),
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        payload = ErrorResponse(code=_status_to_code(exc.status_code), message=message)
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(mode="json"),
            headers=exc.headers,
        )


def _status_to_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_failed",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(status_code, "error")
