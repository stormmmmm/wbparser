"""FastAPI application factory and entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from api.backends import MaxBackend, build_backend
from api.config import Settings, get_settings
from api.errors import install_error_handlers
from api.models.accounts import Account, AccountSessionState
from api.routers import accounts, channels, health, jobs, media, posts, schedules, webhooks
from api.storage import Storage


def _mask_phone(phone: str) -> str | None:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 4:
        return None
    masked_body = "*" * max(len(digits) - 5, 1)
    return f"+{digits[0]}{masked_body}{digits[-4:]}"


def create_app(
    settings: Settings | None = None,
    *,
    backend: MaxBackend | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    """Build the FastAPI application configured for the MAX userbot gateway."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            try:
                resumed = await app.state.backend.resume_existing_accounts()
            except Exception:  # pragma: no cover - resume best-effort
                resumed = []
            now = datetime.now(timezone.utc)
            for entry in resumed:
                account_id = entry.get("account_id")
                if not account_id:
                    continue
                try:
                    app.state.storage.get_account(account_id)
                    continue  # already present (e.g. test fixtures)
                except Exception:
                    pass
                app.state.storage.add_account(
                    Account(
                        account_id=account_id,
                        max_user_id=entry.get("max_user_id"),
                        phone_masked=_mask_phone(entry.get("phone") or ""),
                        username=entry.get("username"),
                        display_name=entry.get("display_name") or "MAX user",
                        status=AccountSessionState.connected,
                        last_activity_at=now,
                        created_at=now,
                    )
                )
            yield
        finally:
            try:
                await app.state.backend.aclose()
            except Exception:  # pragma: no cover - shutdown best-effort
                pass

    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=settings.api_description,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.storage = storage or Storage()
    app.state.backend = backend or build_backend(settings)

    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(accounts.router)
    app.include_router(channels.router)
    app.include_router(media.router)
    app.include_router(posts.router)
    app.include_router(schedules.router)
    app.include_router(jobs.router)
    app.include_router(webhooks.router)

    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint that starts uvicorn."""
    import uvicorn  # local import to keep startup cheap during testing

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
