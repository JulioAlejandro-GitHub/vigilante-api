from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.events import ManualReviewRead, get_manual_review, list_manual_reviews

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
