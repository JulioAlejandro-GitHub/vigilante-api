from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.events import TimelineEventRead, get_timeline_by_source_event_id, list_timeline
from app.services.live_event_projection_service import project_recent_live_recognition_events
from app.services.rbac_service import require_sensitive_read
from app.services.scope_service import filter_items_by_scope, require_item_scope, require_scope_access

router = APIRouter(prefix="/api/v1/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEventRead])
def get_timeline(
    event_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    organization_id: str | None = None,
    site_id: str | None = None,
    case_id: str | None = None,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> list[TimelineEventRead]:
    require_sensitive_read(current_user)
    if organization_id or site_id:
        require_scope_access(current_user, organization_id=organization_id, site_id=site_id)
    project_recent_live_recognition_events(session, scope_hint=current_user)
    items = list_timeline(
        session,
        limit=limit,
        event_type=event_type,
        camera_id=camera_id,
        subject_id=subject_id,
        organization_id=organization_id,
        site_id=site_id,
        case_id=case_id,
    )
    return evidence_resolution.enrich_list(filter_items_by_scope(current_user, items))


@router.get("/{source_event_id}", response_model=TimelineEventRead)
def get_timeline_item(
    source_event_id: str,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> TimelineEventRead:
    require_sensitive_read(current_user)
    project_recent_live_recognition_events(session, scope_hint=current_user)
    item = get_timeline_by_source_event_id(session, source_event_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    require_item_scope(current_user, item)
    return evidence_resolution.enrich(item)
