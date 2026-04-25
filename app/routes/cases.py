from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.case_record_service import CaseRecordRead, get_case, list_cases
from app.services.case_lifecycle_service import (
    CaseCloseRequest,
    CaseReopenRequest,
    CaseStatusChangeRequest,
    change_case_status,
    close_case,
    reopen_case,
)
from app.services.case_relation_service import (
    list_case_related_reviews,
    list_case_related_suggestions,
    list_case_timeline,
)
from app.services.events import CaseSuggestionRead, ManualReviewRead, TimelineEventRead
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


@router.get("", response_model=list[CaseRecordRead])
def get_case_list(
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[CaseRecordRead]:
    return list_cases(session, limit=limit)


@router.get("/{case_id}", response_model=CaseRecordRead)
def get_case_item(case_id: str, session: Session = Depends(session_dependency)) -> CaseRecordRead:
    try:
        return get_case(session, case_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{case_id}/status", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def change_case_status_item(
    case_id: str,
    request: CaseStatusChangeRequest,
    session: Session = Depends(session_dependency),
) -> CaseRecordRead:
    try:
        return change_case_status(session, case_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{case_id}/close", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def close_case_item(
    case_id: str,
    request: CaseCloseRequest,
    session: Session = Depends(session_dependency),
) -> CaseRecordRead:
    try:
        return close_case(session, case_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{case_id}/reopen", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def reopen_case_item(
    case_id: str,
    request: CaseReopenRequest,
    session: Session = Depends(session_dependency),
) -> CaseRecordRead:
    try:
        return reopen_case(session, case_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{case_id}/timeline", response_model=list[TimelineEventRead])
def get_case_timeline(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[TimelineEventRead]:
    try:
        return list_case_timeline(session, case_id, limit=limit)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/reviews", response_model=list[ManualReviewRead])
def get_case_reviews(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[ManualReviewRead]:
    try:
        return list_case_related_reviews(session, case_id, limit=limit)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/suggestions", response_model=list[CaseSuggestionRead])
def get_case_suggestions(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[CaseSuggestionRead]:
    try:
        return list_case_related_suggestions(session, case_id, limit=limit)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
