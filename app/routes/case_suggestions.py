from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.case_record_service import CaseRecordRead, PromoteCaseSuggestionRequest
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    get_case_suggestion,
    list_case_suggestions,
    promote_case_suggestion,
    resolve_case_suggestion,
)
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.events import CaseSuggestionRead
from app.services.rbac_service import require_analyst, require_sensitive_read, require_supervisor
from app.services.scope_service import filter_items_by_scope, require_item_scope
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

router = APIRouter(prefix="/api/v1/case-suggestions", tags=["case-suggestions"])


@router.get("", response_model=list[CaseSuggestionRead])
def get_case_suggestion_queue(
    status: str | None = None,
    suggestion_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> list[CaseSuggestionRead]:
    require_sensitive_read(current_user)
    items = list_case_suggestions(
        session,
        limit=limit,
        offset=offset,
        status=status,
        suggestion_type=suggestion_type,
        camera_id=camera_id,
        subject_id=subject_id,
    )
    return evidence_resolution.enrich_list(filter_items_by_scope(current_user, items))


@router.get("/{suggestion_id}", response_model=CaseSuggestionRead)
def get_case_suggestion_item(
    suggestion_id: str,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseSuggestionRead:
    require_sensitive_read(current_user)
    item = get_case_suggestion(session, suggestion_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Case suggestion not found")
    require_item_scope(current_user, item)
    return evidence_resolution.enrich(item)


@router.post("/{suggestion_id}/resolve", response_model=CaseSuggestionRead, status_code=status.HTTP_200_OK)
def resolve_case_suggestion_item(
    suggestion_id: str,
    request: CaseSuggestionResolutionRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseSuggestionRead:
    try:
        require_analyst(current_user)
        current = get_case_suggestion(session, suggestion_id)
        if current is None:
            raise WorkflowNotFoundError("Case suggestion not found")
        require_item_scope(current_user, current, operate=True)
        auth_request = request.model_copy(
            update={
                "resolved_by": current_user.username,
                "resolved_by_user_id": current_user.user_id,
            }
        )
        return evidence_resolution.enrich(resolve_case_suggestion(session, suggestion_id, auth_request))
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
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> CaseRecordRead:
    try:
        require_supervisor(current_user)
        current = get_case_suggestion(session, suggestion_id)
        if current is None:
            raise WorkflowNotFoundError("Case suggestion not found")
        require_item_scope(current_user, current, operate=True)
        auth_request = request.model_copy(
            update={
                "resolved_by": current_user.username,
                "resolved_by_user_id": current_user.user_id,
            }
        )
        return evidence_resolution.enrich(promote_case_suggestion(session, suggestion_id, auth_request))
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
