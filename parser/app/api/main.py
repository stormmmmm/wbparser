from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.routes_admin import router as admin_router
from app.api.routes_health import router as health_router
from app.api.routes_metrics import router as metrics_router
from app.api.routes_posts import router as posts_router
from app.api.routes_products import router as products_router
from app.api.routes_webhooks_maxapi import router as webhooks_maxapi_router
from app.config import get_settings
from app.db.session import init_db
from app.logging_config import setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    app = FastAPI(title="WB Parser API", version=__version__)

    @app.on_event("startup")
    def on_startup() -> None:
        init_db()

    app.include_router(health_router)
    app.include_router(posts_router)
    app.include_router(products_router)
    app.include_router(metrics_router)
    app.include_router(webhooks_maxapi_router)
    app.include_router(admin_router)
    return app


app = create_app()
