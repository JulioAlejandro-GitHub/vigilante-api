from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings
from app.logging_config import configure_logging
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.camera_recommendations import router as camera_recommendations_router
from app.routes.cameras import router as cameras_router
from app.routes.case_notes import router as case_notes_router
from app.routes.cases import router as cases_router
from app.routes.case_suggestions import router as case_suggestions_router
from app.routes.dashboard import router as dashboard_router
from app.routes.health import router as health_router
from app.routes.manual_reviews import router as manual_reviews_router
from app.routes.timeline import router as timeline_router


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        runtime_level_path=settings.runtime_log_level_path,
        watch=settings.app_env != "test",
        poll_seconds=settings.runtime_log_level_poll_seconds,
    )
    app = FastAPI(title=settings.app_name)
    app.include_router(admin_router)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(cameras_router)
    app.include_router(camera_recommendations_router)
    app.include_router(timeline_router)
    app.include_router(manual_reviews_router)
    app.include_router(case_suggestions_router)
    app.include_router(case_notes_router)
    app.include_router(cases_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
