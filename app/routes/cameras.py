from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.camera_config_service import CameraRead, get_camera, list_cameras
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.rbac_service import require_sensitive_read
from app.services.scope_service import filter_items_by_scope, require_item_scope

router = APIRouter(prefix="/api/v1/cameras", tags=["cameras"])


@router.get("", response_model=list[CameraRead])
def get_camera_list(
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CameraRead]:
    require_sensitive_read(current_user)
    settings = get_settings()
    return filter_items_by_scope(current_user, list_cameras(session, limit=min(limit, settings.max_query_limit)))


@router.get("/{camera_id}", response_model=CameraRead)
def get_camera_item(
    camera_id: str,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CameraRead:
    require_sensitive_read(current_user)
    item = get_camera(session, camera_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    require_item_scope(current_user, item)
    return item
