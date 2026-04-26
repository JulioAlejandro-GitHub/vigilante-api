from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.events import ManualReviewRead, get_manual_review, list_manual_reviews
from app.services.manual_review_service import ManualReviewResolutionRequest, resolve_manual_review
from app.services.rbac_service import require_analyst, require_sensitive_read
from app.services.scope_service import filter_items_by_scope, require_item_scope
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

router = APIRouter(prefix="/api/v1/manual-reviews", tags=["manual-reviews"])


@router.get("", response_model=list[ManualReviewRead])
def get_manual_review_queue(
    status: str | None = None,
    review_type: str | None = None,
    priority: int | None = Query(default=None, ge=1, le=5),
    camera_id: str | None = None,
    subject_id: str | None = None,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ManualReviewRead]:
    require_sensitive_read(current_user)
    items = list_manual_reviews(
        session,
        limit=limit,
        offset=offset,
        status=status,
        review_type=review_type,
        priority=priority,
        camera_id=camera_id,
        subject_id=subject_id,
    )
    return filter_items_by_scope(current_user, items)


@router.get("/{review_id}", response_model=ManualReviewRead)
def get_manual_review_item(
    review_id: str,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> ManualReviewRead:
    require_sensitive_read(current_user)
    item = get_manual_review(session, review_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Manual review not found")
    require_item_scope(current_user, item)
    return item


@router.post("/{review_id}/resolve", response_model=ManualReviewRead, status_code=status.HTTP_200_OK)
def resolve_manual_review_item(
    review_id: str,
    request: ManualReviewResolutionRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> ManualReviewRead:
    try:
        require_analyst(current_user)
        current = get_manual_review(session, review_id)
        if current is None:
            raise WorkflowNotFoundError("Manual review not found")
        require_item_scope(current_user, current, operate=True)
        auth_request = request.model_copy(
            update={
                "resolved_by": current_user.username,
                "resolved_by_user_id": current_user.user_id,
            }
        )
        return resolve_manual_review(session, review_id, auth_request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
