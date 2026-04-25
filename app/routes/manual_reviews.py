from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.events import ManualReviewRead, get_manual_review, list_manual_reviews
from app.services.manual_review_service import ManualReviewResolutionRequest, resolve_manual_review
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

router = APIRouter(prefix="/api/v1/manual-reviews", tags=["manual-reviews"])


@router.get("", response_model=list[ManualReviewRead])
def get_manual_review_queue(
    review_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[ManualReviewRead]:
    return list_manual_reviews(
        session,
        limit=limit,
        review_type=review_type,
        camera_id=camera_id,
        subject_id=subject_id,
    )


@router.get("/{review_id}", response_model=ManualReviewRead)
def get_manual_review_item(review_id: str, session: Session = Depends(session_dependency)) -> ManualReviewRead:
    item = get_manual_review(session, review_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Manual review not found")
    return item


@router.post("/{review_id}/resolve", response_model=ManualReviewRead, status_code=status.HTTP_200_OK)
def resolve_manual_review_item(
    review_id: str,
    request: ManualReviewResolutionRequest,
    session: Session = Depends(session_dependency),
) -> ManualReviewRead:
    try:
        return resolve_manual_review(session, review_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
