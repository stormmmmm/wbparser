"""Bearer-token authentication for the MAX userbot gateway."""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings, get_settings
from api.errors import UnauthorizedError

_bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")


def require_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    """Validate the Authorization: Bearer <token> header against the configured token."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Missing bearer token.")
    if credentials.credentials != settings.auth_token:
        raise UnauthorizedError("Invalid bearer token.")
    request.state.principal = "operator"
    return credentials.credentials
