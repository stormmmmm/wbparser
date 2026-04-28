"""GET /health"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.config import Settings, get_settings
from api.models.health import HealthResponse, HealthStatus, UpstreamStatus

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health check",
    response_model=HealthResponse,
    operation_id="getHealth",
)
def get_health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    upstream = UpstreamStatus.ok if settings.max_upstream_url else None
    return HealthResponse(
        status=HealthStatus.ok,
        version=settings.api_version,
        time=datetime.now(timezone.utc),
        max_upstream=upstream,
    )
