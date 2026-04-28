"""Health-check response model."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from api.models.common import APIModel


class HealthStatus(str, Enum):
    ok = "ok"
    degraded = "degraded"


class UpstreamStatus(str, Enum):
    ok = "ok"
    degraded = "degraded"
    unavailable = "unavailable"


class HealthResponse(APIModel):
    status: HealthStatus
    version: str
    time: datetime = Field(default_factory=lambda: datetime.utcnow())
    max_upstream: UpstreamStatus | None = None
