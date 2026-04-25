from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.case_record_service import CaseRecordRead, PromoteCaseSuggestionRequest
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    get_case_suggestion,
    list_case_suggestions,
    promote_case_suggestion,
    resolve_case_suggestion,
)
from app.services.events import CaseSuggestionRead
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

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


@router.post("/{suggestion_id}/resolve", response_model=CaseSuggestionRead, status_code=status.HTTP_200_OK)
def resolve_case_suggestion_item(
    suggestion_id: str,
    request: CaseSuggestionResolutionRequest,
    session: Session = Depends(session_dependency),
) -> CaseSuggestionRead:
    try:
        return resolve_case_suggestion(session, suggestion_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{suggestion_id}/promote", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def promote_case_suggestion_item(
    suggestion_id: str,
    request: PromoteCaseSuggestionRequest,
    session: Session = Depends(session_dependency),
) -> CaseRecordRead:
    try:
        return promote_case_suggestion(session, suggestion_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
