from __future__ import annotations

import logging

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
from app.services.evidence_ref_classifier import dedupe_evidence_refs, extract_evidence_refs
from app.services.case_relation_service import (
    list_case_related_reviews,
    list_case_related_suggestions,
    list_case_timeline,
)
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.events import CaseSuggestionRead, ManualReviewRead, TimelineEventRead, get_timeline_by_source_event_id
from app.services.media_models import EvidenceMediaPage
from app.services.live_event_projection_service import project_recent_live_recognition_events
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
logger = logging.getLogger(__name__)


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
    limit: int = Query(default=25, ge=1),
    offset: int = Query(default=0, ge=0),
    include_evidence: bool = Query(default=False),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> list[CaseRecordRead]:
    require_sensitive_read(current_user)
    if organization_id or site_id:
        require_scope_access(current_user, organization_id=organization_id, site_id=site_id)
    project_recent_live_recognition_events(session, scope_hint=current_user)
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
    page = scoped[offset : offset + min(limit, settings.max_query_limit)]
    if include_evidence:
        page = evidence_resolution.enrich_list(page)
    logger.info(
        "cases_loaded items=%s limit=%s offset=%s include_evidence=%s media_requested=%s media_resolved=%s media_failed=%s next_offset=%s",
        len(page),
        limit,
        offset,
        include_evidence,
        evidence_resolution.stats.requested,
        evidence_resolution.stats.resolved,
        evidence_resolution.stats.failed,
        offset + len(page) if len(page) >= limit else None,
    )
    return page


@router.get("/{case_id}", response_model=CaseDetailRead)
def get_case_item(
    case_id: str,
    recent_limit: int = Query(default=10, ge=1),
    expand: str = Query(default="all", pattern="^(all|summary)$"),
    include_evidence: bool = Query(default=True),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseDetailRead:
    try:
        require_sensitive_read(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        if expand == "summary":
            base = get_case(session, case_id)
            item = CaseDetailRead(**base.model_dump())
        else:
            item = get_case_detail(session, case_id, recent_limit=recent_limit)
        require_item_scope(current_user, item)
        item.reviews = filter_items_by_scope(current_user, item.reviews)
        item.suggestions = filter_items_by_scope(current_user, item.suggestions)
        item.timeline = filter_items_by_scope(current_user, item.timeline)
        if include_evidence:
            item = evidence_resolution.enrich(item)
        logger.info(
            "case_detail_loaded case_id=%s expand=%s include_evidence=%s timeline=%s reviews=%s suggestions=%s media_requested=%s media_resolved=%s media_failed=%s",
            case_id,
            expand,
            include_evidence,
            len(item.timeline),
            len(item.reviews),
            len(item.suggestions),
            evidence_resolution.stats.requested,
            evidence_resolution.stats.resolved,
            evidence_resolution.stats.failed,
        )
        return item
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{case_id}/assign", response_model=CaseRecordRead, status_code=status.HTTP_200_OK)
def assign_case_item(
    case_id: str,
    request: CaseAssignRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseRecordRead:
    try:
        require_analyst(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
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
        return evidence_resolution.enrich(assign_case(session, case_id, auth_request))
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
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseRecordRead:
    try:
        require_analyst(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        require_case_unassignment_permission(current_user, previous_assigned_to=case.assigned_to)
        auth_request = request.model_copy(
            update={
                "assigned_by": current_user.username,
                "assigned_by_user_id": current_user.user_id,
            }
        )
        return evidence_resolution.enrich(unassign_case(session, case_id, auth_request))
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
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseRecordRead:
    try:
        require_analyst(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
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
        return evidence_resolution.enrich(change_case_status(session, case_id, auth_request))
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
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseRecordRead:
    try:
        require_supervisor(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        auth_request = request.model_copy(
            update={
                "changed_by": current_user.username,
                "changed_by_user_id": current_user.user_id,
            }
        )
        return evidence_resolution.enrich(close_case(session, case_id, auth_request))
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
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseRecordRead:
    try:
        require_supervisor(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        auth_request = request.model_copy(
            update={
                "changed_by": current_user.username,
                "changed_by_user_id": current_user.user_id,
            }
        )
        return evidence_resolution.enrich(reopen_case(session, case_id, auth_request))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{case_id}/evidence", response_model=EvidenceMediaPage)
def get_case_evidence(
    case_id: str,
    source_event_id: str | None = None,
    limit: int = Query(default=6, ge=1),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> EvidenceMediaPage:
    try:
        require_sensitive_read(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        refs = _case_evidence_refs(session, case_id, source_event_id=source_event_id, current_user=current_user)

        settings = get_settings()
        safe_limit = max(1, min(limit, settings.max_query_limit))
        safe_offset = max(0, offset)
        page_refs = refs[safe_offset : safe_offset + safe_limit]
        media_items = evidence_resolution.resolve_refs(page_refs)
        next_offset = safe_offset + len(media_items) if safe_offset + len(media_items) < len(refs) else None
        logger.info(
            "case_evidence_loaded case_id=%s source_event_id=%s items=%s total_refs=%s limit=%s offset=%s media_requested=%s media_resolved=%s media_failed=%s next_offset=%s",
            case_id,
            source_event_id,
            len(media_items),
            len(refs),
            safe_limit,
            safe_offset,
            evidence_resolution.stats.requested,
            evidence_resolution.stats.resolved,
            evidence_resolution.stats.failed,
            next_offset,
        )
        return EvidenceMediaPage(items=media_items, limit=safe_limit, offset=safe_offset, next_offset=next_offset, total_refs=len(refs))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/timeline", response_model=list[TimelineEventRead])
def get_case_timeline(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    include_evidence: bool = Query(default=True),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> list[TimelineEventRead]:
    try:
        require_sensitive_read(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        items = filter_items_by_scope(current_user, list_case_timeline(session, case_id, limit=limit, offset=offset))
        if include_evidence:
            items = evidence_resolution.enrich_list(items)
        logger.info(
            "case_timeline_loaded case_id=%s items=%s limit=%s offset=%s include_evidence=%s media_requested=%s media_resolved=%s media_failed=%s next_offset=%s",
            case_id,
            len(items),
            limit,
            offset,
            include_evidence,
            evidence_resolution.stats.requested,
            evidence_resolution.stats.resolved,
            evidence_resolution.stats.failed,
            offset + len(items) if len(items) >= limit else None,
        )
        return items
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/reviews", response_model=list[ManualReviewRead])
def get_case_reviews(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    include_evidence: bool = Query(default=True),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> list[ManualReviewRead]:
    try:
        require_sensitive_read(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        items = filter_items_by_scope(current_user, list_case_related_reviews(session, case_id, limit=limit, offset=offset))
        if include_evidence:
            items = evidence_resolution.enrich_list(items)
        logger.info(
            "case_reviews_loaded case_id=%s items=%s limit=%s offset=%s include_evidence=%s media_requested=%s media_resolved=%s media_failed=%s next_offset=%s",
            case_id,
            len(items),
            limit,
            offset,
            include_evidence,
            evidence_resolution.stats.requested,
            evidence_resolution.stats.resolved,
            evidence_resolution.stats.failed,
            offset + len(items) if len(items) >= limit else None,
        )
        return items
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/suggestions", response_model=list[CaseSuggestionRead])
def get_case_suggestions(
    case_id: str,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    include_evidence: bool = Query(default=True),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> list[CaseSuggestionRead]:
    try:
        require_sensitive_read(current_user)
        project_recent_live_recognition_events(session, scope_hint=current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        items = filter_items_by_scope(current_user, list_case_related_suggestions(session, case_id, limit=limit, offset=offset))
        if include_evidence:
            items = evidence_resolution.enrich_list(items)
        logger.info(
            "case_suggestions_loaded case_id=%s items=%s limit=%s offset=%s include_evidence=%s media_requested=%s media_resolved=%s media_failed=%s next_offset=%s",
            case_id,
            len(items),
            limit,
            offset,
            include_evidence,
            evidence_resolution.stats.requested,
            evidence_resolution.stats.resolved,
            evidence_resolution.stats.failed,
            offset + len(items) if len(items) >= limit else None,
        )
        return items
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _case_evidence_refs(
    session: Session,
    case_id: str,
    *,
    source_event_id: str | None,
    current_user: CurrentUser,
) -> list[str]:
    settings = get_settings()
    collection_limit = settings.max_query_limit
    refs: list[str] = []

    if source_event_id:
        event = get_timeline_by_source_event_id(session, source_event_id)
        if event is not None:
            require_item_scope(current_user, event)
            refs.extend(extract_evidence_refs(event, max_refs=collection_limit))

    case = get_case(session, case_id)
    refs.extend(extract_evidence_refs(case, max_refs=collection_limit))

    for event in filter_items_by_scope(current_user, list_case_timeline(session, case_id, limit=collection_limit)):
        refs.extend(extract_evidence_refs(event, max_refs=collection_limit))

    for review in filter_items_by_scope(current_user, list_case_related_reviews(session, case_id, limit=collection_limit)):
        refs.extend(extract_evidence_refs(review, max_refs=collection_limit))

    for suggestion in filter_items_by_scope(current_user, list_case_related_suggestions(session, case_id, limit=collection_limit)):
        refs.extend(extract_evidence_refs(suggestion, max_refs=collection_limit))

    return dedupe_evidence_refs(refs)[:collection_limit]
