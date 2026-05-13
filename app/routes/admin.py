from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from app.config import get_settings
from app.logging_config import current_log_level_name, write_runtime_log_level
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.rbac_service import require_supervisor

router = APIRouter(prefix="/admin", tags=["admin"])


class LogLevelUpdate(BaseModel):
    level: str


class LogLevelRead(BaseModel):
    level: str
    runtime_path: str


@router.get("/log-level", response_model=LogLevelRead)
def get_log_level(current_user: CurrentUser = Depends(get_current_user)) -> LogLevelRead:
    require_supervisor(current_user)
    settings = get_settings()
    return LogLevelRead(level=current_log_level_name(), runtime_path=settings.runtime_log_level_path)


@router.post("/log-level", response_model=LogLevelRead)
def update_log_level(
    request: LogLevelUpdate,
    current_user: CurrentUser = Depends(get_current_user),
) -> LogLevelRead:
    require_supervisor(current_user)
    settings = get_settings()
    try:
        level = write_runtime_log_level(
            settings.runtime_log_level_path,
            request.level,
            source=f"admin_http:{current_user.username}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LogLevelRead(level=level, runtime_path=settings.runtime_log_level_path)
