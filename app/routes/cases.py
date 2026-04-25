from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.case_assignment_service import CaseAssignRequest, CaseUnassignRequest, assign_case, unassign_case
from app.services.case_query_service import CaseDetailRead, get_case_detail, list_cases_filtered
from app.services.case_record_service import CaseRecordRead
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
    status: str | None = None,
    assigned_to: str | None = None,
    priority: int | None = Query(default=None, ge=1, le=5),
    severity: str | None = None,
    case_type: str | None = None,
    organization_id: str | None = None,
    site_id: str | None = None,
    q: str | None = None,
    sort_by: str = Query(default="updated_at", pattern="^(updated_at|opened_at|priority)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(session_dependency),
) -> list[CaseRecordRead]:
    return list_cases_filtered(
        session,
        limit=limit,
        offset=offset,
        status=status,
        assigned_to=assigned_to,
        priority=priority,
        severity=severity,
        case_type=case_type,
        organization_id=organization_id,
        site_id=site_id,
        q=q,
        sort_by=sort_by,  # type: ignore[arg-type]
        sort_order=sort_order,  # type: ignore[arg-type]
    )


@router.get("/{case_id}", response_model=CaseDetailRead)
def get_case_item(
    case_id: str,
    recent_limit: int = Query(default=10, ge=1),
    session: Session = Depends(session_dependency),
) -> CaseDetailRead:
    try:
        return get_case_detail(session, case_id, recent_limit=recent_limit)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{case_id}/assign", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def assign_case_item(
    case_id: str,
    request: CaseAssignRequest,
    session: Session = Depends(session_dependency),
) -> CaseRecordRead:
    try:
        return assign_case(session, case_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{case_id}/unassign", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def unassign_case_item(
    case_id: str,
    request: CaseUnassignRequest,
    session: Session = Depends(session_dependency),
) -> CaseRecordRead:
    try:
        return unassign_case(session, case_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
