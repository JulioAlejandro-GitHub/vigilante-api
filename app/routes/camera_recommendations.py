from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.models import Camera
from app.services.camera_recommendation_apply_service import (
    CameraRecommendationApplyRequest,
    CameraRecommendationApplyResult,
    CameraRecommendationPreview,
    CameraRecommendationRollbackRequest,
    apply_recommendation,
    preview_recommendation,
    rollback_recommendation,
)
from app.services.camera_recommendation_workflow_service import (
    CameraRecommendationDecisionRequest,
    CameraRecommendationRead,
    approve_recommendation,
    get_recommendation,
    list_recommendations,
    reject_recommendation,
)
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.rbac_service import require_analyst, require_sensitive_read
from app.services.scope_service import require_item_scope
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

router = APIRouter(prefix="/api/v1/camera-recommendations", tags=["camera-recommendations"])


@router.get("", response_model=list[CameraRecommendationRead])
def get_camera_recommendations(
    status: str | None = None,
    camera_id: str | None = None,
    actionable: bool | None = None,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CameraRecommendationRead]:
    require_sensitive_read(current_user)
    items = list_recommendations(
        session,
        status=status,
        camera_id=camera_id,
        actionable=actionable,
        limit=min(limit, get_settings().max_query_limit),
        offset=offset,
    )
    return [item for item in items if _recommendation_in_scope(session, current_user, item)]


@router.get("/{recommendation_id}", response_model=CameraRecommendationRead)
def get_camera_recommendation_item(
    recommendation_id: str,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CameraRecommendationRead:
    try:
        require_sensitive_read(current_user)
        item = get_recommendation(session, recommendation_id)
        _require_recommendation_scope(session, current_user, item)
        return item
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{recommendation_id}/preview", response_model=CameraRecommendationPreview)
def preview_camera_recommendation_item(
    recommendation_id: str,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CameraRecommendationPreview:
    try:
        require_sensitive_read(current_user)
        item = get_recommendation(session, recommendation_id)
        _require_recommendation_scope(session, current_user, item)
        return preview_recommendation(session, recommendation_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{recommendation_id}/approve", response_model=CameraRecommendationRead, status_code=status.HTTP_200_OK)
def approve_camera_recommendation_item(
    recommendation_id: str,
    request: CameraRecommendationDecisionRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CameraRecommendationRead:
    try:
        require_analyst(current_user)
        item = get_recommendation(session, recommendation_id)
        _require_recommendation_scope(session, current_user, item, operate=True)
        return approve_recommendation(
            session,
            recommendation_id,
            actor=current_user.username,
            actor_user_id=current_user.user_id,
            comment=request.comment,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{recommendation_id}/reject", response_model=CameraRecommendationRead, status_code=status.HTTP_200_OK)
def reject_camera_recommendation_item(
    recommendation_id: str,
    request: CameraRecommendationDecisionRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CameraRecommendationRead:
    try:
        require_analyst(current_user)
        item = get_recommendation(session, recommendation_id)
        _require_recommendation_scope(session, current_user, item, operate=True)
        return reject_recommendation(
            session,
            recommendation_id,
            actor=current_user.username,
            actor_user_id=current_user.user_id,
            comment=request.comment,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{recommendation_id}/apply", response_model=CameraRecommendationApplyResult, status_code=status.HTTP_200_OK)
def apply_camera_recommendation_item(
    recommendation_id: str,
    request: CameraRecommendationApplyRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CameraRecommendationApplyResult:
    try:
        require_analyst(current_user)
        item = get_recommendation(session, recommendation_id)
        _require_recommendation_scope(session, current_user, item, operate=True)
        return apply_recommendation(
            session,
            recommendation_id,
            actor=current_user.username,
            actor_user_id=current_user.user_id,
            comment=request.comment,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{recommendation_id}/rollback", response_model=CameraRecommendationApplyResult, status_code=status.HTTP_200_OK)
def rollback_camera_recommendation_item(
    recommendation_id: str,
    request: CameraRecommendationRollbackRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CameraRecommendationApplyResult:
    try:
        require_analyst(current_user)
        item = get_recommendation(session, recommendation_id)
        _require_recommendation_scope(session, current_user, item, operate=True)
        return rollback_recommendation(
            session,
            recommendation_id,
            actor=current_user.username,
            actor_user_id=current_user.user_id,
            comment=request.comment,
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _recommendation_in_scope(session: Session, current_user: CurrentUser, item: CameraRecommendationRead) -> bool:
    camera = _camera_for_recommendation(session, item)
    if camera is None:
        return False
    try:
        require_item_scope(current_user, camera)
    except HTTPException:
        return False
    return True


def _require_recommendation_scope(
    session: Session,
    current_user: CurrentUser,
    item: CameraRecommendationRead,
    *,
    operate: bool = False,
) -> None:
    camera = _camera_for_recommendation(session, item)
    if camera is None:
        raise WorkflowNotFoundError("Recommendation camera not found")
    require_item_scope(current_user, camera, operate=operate)


def _camera_for_recommendation(session: Session, item: CameraRecommendationRead) -> Camera | None:
    try:
        camera_id = UUID(item.camera_id)
    except ValueError:
        return None
    return session.get(Camera, camera_id)
