from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.events import CaseSuggestionRead, get_case_suggestion, list_case_suggestions

router = APIRouter(prefix="/api/v1/case-suggestions", tags=["case-suggestions"])


@router.get("", response_model=list[CaseSuggestionRead])
def get_case_suggestion_queue(
    suggestion_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[CaseSuggestionRead]:
    return list_case_suggestions(
        session,
        limit=limit,
        suggestion_type=suggestion_type,
        camera_id=camera_id,
        subject_id=subject_id,
    )


@router.get("/{suggestion_id}", response_model=CaseSuggestionRead)
def get_case_suggestion_item(suggestion_id: str, session: Session = Depends(session_dependency)) -> CaseSuggestionRead:
    item = get_case_suggestion(session, suggestion_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Case suggestion not found")
    return item
