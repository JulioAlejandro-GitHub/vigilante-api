from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.camera_latest_frame_service import CameraLatestFrameRead, list_camera_latest_frames
from app.services.camera_config_service import CameraRead, get_camera, list_cameras
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.rbac_service import require_sensitive_read
from app.services.scope_service import filter_items_by_scope, require_item_scope

router = APIRouter(prefix="/api/v1/cameras", tags=["cameras"])


@router.get("", response_model=list[CameraRead])
def get_camera_list(
    limit: int = Query(default=12, ge=1),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CameraRead]:
    require_sensitive_read(current_user)
    settings = get_settings()
    return filter_items_by_scope(current_user, list_cameras(session, limit=min(limit, settings.max_query_limit), offset=offset))


@router.get("/latest-frames", response_model=list[CameraLatestFrameRead])
def get_camera_latest_frame_list(
    camera_id: list[str] = Query(default_factory=list),
    include_media: bool = Query(default=True),
    limit: int = Query(default=12, ge=1),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> list[CameraLatestFrameRead]:
    require_sensitive_read(current_user)
    settings = get_settings()
    if camera_id:
        candidates = [item for raw_camera_id in camera_id if (item := get_camera(session, raw_camera_id)) is not None]
    else:
        candidates = list_cameras(session, limit=min(limit, settings.max_query_limit), offset=0)
    scoped = filter_items_by_scope(current_user, candidates)
    scoped_ids = [item.camera_id for item in scoped]
    return list_camera_latest_frames(
        session,
        camera_ids=scoped_ids,
        evidence_resolution=evidence_resolution,
        include_media=include_media,
        settings=settings,
    )


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
