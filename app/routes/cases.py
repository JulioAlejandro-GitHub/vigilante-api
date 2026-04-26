from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.auth_service import InactiveUserError, ensure_user_active, find_user_by_login, user_username
from app.services.case_assignment_service import CaseAssignRequest, CaseUnassignRequest, assign_case, unassign_case
from app.services.case_query_service import CaseDetailRead, get_case_detail, list_cases_filtered
from app.services.case_record_service import CaseRecordRead, get_case
from app.services.case_lifecycle_service import (
    CaseCloseRequest,
    CaseReopenRequest,
    CaseStatusChangeRequest,
    change_case_status,
    close_case,
    reopen_case,
)
from app.services.current_user_service import CurrentUser, build_current_user, get_current_user
from app.services.case_relation_service import (
    list_case_related_reviews,
    list_case_related_suggestions,
    list_case_timeline,
)
from app.services.events import CaseSuggestionRead, ManualReviewRead, TimelineEventRead
from app.services.rbac_service import (
    require_analyst,
    require_case_assignment_permission,
    require_case_unassignment_permission,
    require_sensitive_read,
    require_supervisor,
)
from app.services.scope_service import filter_items_by_scope, require_item_scope, require_scope_access, scope_allows
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
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CaseRecordRead]:
    require_sensitive_read(current_user)
    if organization_id or site_id:
        require_scope_access(current_user, organization_id=organization_id, site_id=site_id)
    settings = get_settings()
    items = list_cases_filtered(
        session,
        limit=settings.max_query_limit,
        offset=0,
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
    scoped = filter_items_by_scope(current_user, items)
    return scoped[offset : offset + min(limit, settings.max_query_limit)]


@router.get("/{case_id}", response_model=CaseDetailRead)
def get_case_item(
    case_id: str,
    recent_limit: int = Query(default=10, ge=1),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CaseDetailRead:
    try:
        require_sensitive_read(current_user)
        item = get_case_detail(session, case_id, recent_limit=recent_limit)
        require_item_scope(current_user, item)
        item.reviews = filter_items_by_scope(current_user, item.reviews)
        item.suggestions = filter_items_by_scope(current_user, item.suggestions)
        item.timeline = filter_items_by_scope(current_user, item.timeline)
        return item
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{case_id}/assign", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def assign_case_item(
    case_id: str,
    request: CaseAssignRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CaseRecordRead:
    try:
        require_analyst(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        target_user = find_user_by_login(session, request.assigned_to)
        if target_user is None:
            raise WorkflowValidationError("assigned_to user not found")
        try:
            ensure_user_active(target_user)
        except InactiveUserError as exc:
            raise WorkflowValidationError("assigned_to user is inactive") from exc
        target_current_user = build_current_user(session, target_user)
        if not scope_allows(
            target_current_user,
            organization_id=case.organization_id or case.case_payload.get("raw_organization_id"),
            site_id=case.site_id or case.case_payload.get("raw_site_id"),
            operate=True,
        ):
            raise WorkflowValidationError("assigned_to user is outside case scope")
        previous_assigned_to = case.assigned_to
        assigned_to = user_username(target_user)
        require_case_assignment_permission(
            current_user,
            assigned_to=assigned_to,
            previous_assigned_to=previous_assigned_to,
        )
        auth_request = request.model_copy(
            update={
                "assigned_to": assigned_to,
                "assigned_to_user_id": str(target_user.user_id),
                "assigned_by": current_user.username,
                "assigned_by_user_id": current_user.user_id,
            }
        )
        return assign_case(session, case_id, auth_request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{case_id}/unassign", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def unassign_case_item(
    case_id: str,
    request: CaseUnassignRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CaseRecordRead:
    try:
        require_analyst(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        require_case_unassignment_permission(current_user, previous_assigned_to=case.assigned_to)
        auth_request = request.model_copy(
            update={
                "assigned_by": current_user.username,
                "assigned_by_user_id": current_user.user_id,
            }
        )
        return unassign_case(session, case_id, auth_request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{case_id}/status", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def change_case_status_item(
    case_id: str,
    request: CaseStatusChangeRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CaseRecordRead:
    try:
        require_analyst(current_user)
        if request.status in {"closed", "reopened"}:
            require_supervisor(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        auth_request = request.model_copy(
            update={
                "changed_by": current_user.username,
                "changed_by_user_id": current_user.user_id,
            }
        )
        return change_case_status(session, case_id, auth_request)
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
    current_user: CurrentUser = Depends(get_current_user),
) -> CaseRecordRead:
    try:
        require_supervisor(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        auth_request = request.model_copy(
            update={
                "changed_by": current_user.username,
                "changed_by_user_id": current_user.user_id,
            }
        )
        return close_case(session, case_id, auth_request)
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
    current_user: CurrentUser = Depends(get_current_user),
) -> CaseRecordRead:
    try:
        require_supervisor(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        auth_request = request.model_copy(
            update={
                "changed_by": current_user.username,
                "changed_by_user_id": current_user.user_id,
            }
        )
        return reopen_case(session, case_id, auth_request)
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
    current_user: CurrentUser = Depends(get_current_user),
) -> list[TimelineEventRead]:
    try:
        require_sensitive_read(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        return filter_items_by_scope(current_user, list_case_timeline(session, case_id, limit=limit))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/reviews", response_model=list[ManualReviewRead])
def get_case_reviews(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ManualReviewRead]:
    try:
        require_sensitive_read(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        return filter_items_by_scope(current_user, list_case_related_reviews(session, case_id, limit=limit))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/suggestions", response_model=list[CaseSuggestionRead])
def get_case_suggestions(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CaseSuggestionRead]:
    try:
        require_sensitive_read(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        return filter_items_by_scope(current_user, list_case_related_suggestions(session, case_id, limit=limit))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
