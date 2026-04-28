"""Runtime configuration for the MAX userbot gateway."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    """Process-wide configuration loaded from environment variables."""

    api_title: str = "MAX Userbot Posting API"
    api_version: str = "1.0.0"
    api_description: str = (
        "Local posting gateway that automates publication to MAX channels "
        "from an authorized user account."
    )
    server_host: str = field(default_factory=lambda: os.getenv("MAXAPI_HOST", "0.0.0.0"))
    server_port: int = field(default_factory=lambda: int(os.getenv("MAXAPI_PORT", "8080")))

    auth_token: str = field(default_factory=lambda: os.getenv("MAXAPI_TOKEN", "dev-token"))
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAXAPI_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    )

    max_upstream_url: str | None = field(
        default_factory=lambda: os.getenv("MAXAPI_UPSTREAM_URL")
    )
    max_upstream_token: str | None = field(
        default_factory=lambda: os.getenv("MAXAPI_UPSTREAM_TOKEN")
    )

    # Upstream backend selection: "memory" (default, in-process stub) or
    # "pymax" (real MAX userbot via the maxapi-python library).
    backend: str = field(default_factory=lambda: os.getenv("MAXAPI_BACKEND", "memory"))
    # Directory PyMax uses to persist per-account session caches.
    pymax_work_dir: str = field(
        default_factory=lambda: os.getenv("MAXAPI_PYMAX_WORK_DIR", "/var/lib/maxapi")
    )
    pymax_device_type: str = field(
        default_factory=lambda: os.getenv("MAXAPI_PYMAX_DEVICE_TYPE", "DESKTOP")
    )
    pymax_app_version: str = field(
        default_factory=lambda: os.getenv("MAXAPI_PYMAX_APP_VERSION", "25.12.13")
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()
