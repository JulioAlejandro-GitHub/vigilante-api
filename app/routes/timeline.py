from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.evidence_ref_classifier import dedupe_evidence_refs, extract_evidence_refs
from app.services.events import TimelineEventRead, get_timeline_by_source_event_id, list_timeline
from app.services.live_event_projection_service import project_recent_live_recognition_events
from app.services.media_models import EvidenceMediaPage
from app.services.rbac_service import require_sensitive_read
from app.services.scope_service import filter_items_by_scope, require_item_scope, require_scope_access

router = APIRouter(prefix="/api/v1/timeline", tags=["timeline"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[TimelineEventRead])
def get_timeline(
    event_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    organization_id: str | None = None,
    site_id: str | None = None,
    case_id: str | None = None,
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
    include_evidence: bool = Query(default=False),
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
        offset=offset,
        event_type=event_type,
        camera_id=camera_id,
        subject_id=subject_id,
        organization_id=organization_id,
        site_id=site_id,
        case_id=case_id,
    )
    scoped = filter_items_by_scope(current_user, items)
    if include_evidence:
        scoped = evidence_resolution.enrich_list(scoped)
    logger.info(
        "timeline_loaded items=%s limit=%s offset=%s include_evidence=%s media_requested=%s media_resolved=%s media_failed=%s next_offset=%s camera_id=%s",
        len(scoped),
        limit,
        offset,
        include_evidence,
        evidence_resolution.stats.requested,
        evidence_resolution.stats.resolved,
        evidence_resolution.stats.failed,
        offset + len(scoped) if len(scoped) >= limit else None,
        camera_id,
    )
    return scoped


@router.get("/{source_event_id}/evidence", response_model=EvidenceMediaPage)
def get_timeline_evidence(
    source_event_id: str,
    limit: int = Query(default=6, ge=1),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
    evidence_resolution: EvidenceResolutionService = Depends(evidence_resolution_service_dependency),
) -> EvidenceMediaPage:
    require_sensitive_read(current_user)
    project_recent_live_recognition_events(session, scope_hint=current_user)
    item = get_timeline_by_source_event_id(session, source_event_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    require_item_scope(current_user, item)

    settings = get_settings()
    safe_limit = max(1, min(limit, settings.max_query_limit))
    safe_offset = max(0, offset)
    refs = dedupe_evidence_refs(extract_evidence_refs(item, max_refs=settings.max_query_limit))
    page_refs = refs[safe_offset : safe_offset + safe_limit]
    media_items = evidence_resolution.resolve_refs(page_refs)
    next_offset = safe_offset + len(media_items) if safe_offset + len(media_items) < len(refs) else None
    logger.info(
        "timeline_evidence_loaded source_event_id=%s items=%s total_refs=%s limit=%s offset=%s media_requested=%s media_resolved=%s media_failed=%s next_offset=%s camera_id=%s",
        source_event_id,
        len(media_items),
        len(refs),
        safe_limit,
        safe_offset,
        evidence_resolution.stats.requested,
        evidence_resolution.stats.resolved,
        evidence_resolution.stats.failed,
        next_offset,
        item.camera_id,
    )
    return EvidenceMediaPage(items=media_items, limit=safe_limit, offset=safe_offset, next_offset=next_offset, total_refs=len(refs))


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
