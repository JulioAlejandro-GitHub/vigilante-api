from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.db import ping_database

router = APIRouter(tags=["health"])


@router.get("/health")
def get_health() -> dict[str, str]:
    ping_database()
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "projection_strategy": "timeline_event_payload",
    }
