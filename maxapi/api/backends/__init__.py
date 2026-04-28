"""Pluggable upstream backends for the MAX userbot gateway."""

from api.backends.memory import InMemoryBackend
from api.backends.protocol import (
    LoginChallengeData,
    MaxBackend,
    UpstreamChannel,
    UpstreamMedia,
    UpstreamMessage,
)
from api.models.media import Media

__all__ = [
    "InMemoryBackend",
    "LoginChallengeData",
    "MaxBackend",
    "UpstreamChannel",
    "UpstreamMedia",
    "UpstreamMessage",
    "media_to_upstream",
]


def media_to_upstream(media: list[Media]) -> list[UpstreamMedia]:
    """Convert gateway-side ``Media`` records into the upstream descriptor used
    by every backend. Carries the backend-specific blob in ``attachment`` so
    e.g. PyMax can re-open the local file uploaded earlier.
    """
    return [
        UpstreamMedia(
            media_id=m.media_id,
            type=m.type,
            status=m.status.value if hasattr(m.status, "value") else str(m.status),
            url=m.url,
            filename=getattr(m, "filename", None),
            mime_type=getattr(m, "mime_type", None),
            size_bytes=getattr(m, "size_bytes", None),
            attachment=getattr(m, "max_attachment", None),
        )
        for m in media
    ]


def build_backend(settings) -> MaxBackend:
    """Instantiate the backend selected via configuration."""
    name = (settings.backend or "memory").lower()
    if name == "memory":
        return InMemoryBackend()
    if name == "pymax":
        # Imported lazily so the optional dependency is only required when
        # the operator opts into the real upstream.
        from api.backends.pymax_backend import PyMaxBackend

        return PyMaxBackend(
            work_dir=settings.pymax_work_dir,
            device_type=settings.pymax_device_type,
            app_version=settings.pymax_app_version,
        )
    raise ValueError(
        f"Unknown MAXAPI_BACKEND={settings.backend!r}; "
        "expected 'memory' or 'pymax'."
    )
