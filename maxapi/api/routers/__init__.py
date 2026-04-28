"""HTTP routers grouped by OpenAPI tag."""

from api.routers import (
    accounts,
    channels,
    health,
    jobs,
    media,
    posts,
    schedules,
    webhooks,
)

__all__ = [
    "accounts",
    "channels",
    "health",
    "jobs",
    "media",
    "posts",
    "schedules",
    "webhooks",
]
