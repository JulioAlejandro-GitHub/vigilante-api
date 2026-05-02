from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TimelineEvent
from app.services.case_record_service import CaseRecordRead, PromoteCaseSuggestionRequest, create_case_from_suggestion
from app.services.events import CASE_SUGGESTION_EVENT_TYPES, CaseSuggestionRead, read_case_suggestion_record
from app.services.live_first_read_service import (
    apply_live_first_order,
    remove_fixture_only_items_when_live,
    timeline_has_live_evidence,
)
from app.services.timeline_service import (
    action_confidence,
    apply_case_promotion,
    apply_case_suggestion_resolution,
    create_audit_timeline_event,
    list_timeline_rows,
    normalized_workflow_payload,
)
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError


CASE_SUGGESTION_ACTION_EVENT_TYPES = {"case_suggestion_resolved", "case_record_created"}
CASE_SUGGESTION_RELEVANT_EVENT_TYPES = CASE_SUGGESTION_EVENT_TYPES | CASE_SUGGESTION_ACTION_EVENT_TYPES
FINAL_CASE_SUGGESTION_STATUSES = {"accepted", "rejected"}


class CaseSuggestionResolutionRequest(BaseModel):
    decision: Literal["accepted", "rejected", "deferred"]
    decision_reason: str
    resolved_by: str | None = None
    resolved_by_user_id: str | None = None
    resolution_payload: dict[str, Any] = Field(default_factory=dict)


def list_case_suggestions(
    session: Session,
    *,
    limit: int,
    offset: int = 0,
    status: str | None = None,
    suggestion_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
) -> list[CaseSuggestionRead]:
    settings = get_settings()
    from app.services.live_case_suggestion_projection_service import project_live_case_suggestions_from_timeline

    project_live_case_suggestions_from_timeline(session)
    safe_limit = max(1, min(limit, settings.max_query_limit))
    safe_offset = max(0, offset)
    base_rows = list_timeline_rows(
        session,
        event_types=CASE_SUGGESTION_EVENT_TYPES,
        descending=False,
    )
    action_rows = list_timeline_rows(
        session,
        event_types=CASE_SUGGESTION_ACTION_EVENT_TYPES,
        descending=False,
    )

    collapsed: dict[str, CaseSuggestionRead] = {}
    for row in base_rows:
        projection = read_case_suggestion_record(row)
        if projection is None:
            continue
        existing = collapsed.get(projection.suggestion_id)
        if existing is None or projection.event_ts >= existing.event_ts:
            collapsed[projection.suggestion_id] = projection

    for row in action_rows:
        timeline_projection = read_action_timeline_projection(row)
        if row.event_type == "case_suggestion_resolved":
            action_payload = (row.payload or {}).get("case_suggestion_resolution")
            if not isinstance(action_payload, dict):
                continue
            suggestion_id_value = action_payload.get("suggestion_id")
            current = collapsed.get(str(suggestion_id_value))
            if current is None:
                continue
            collapsed[str(suggestion_id_value)] = apply_case_suggestion_resolution(
                current,
                action_payload,
                timeline_projection.source_event_id,
            )
            continue

        if row.event_type == "case_record_created":
            action_payload = (row.payload or {}).get("case_record_created")
            if not isinstance(action_payload, dict):
                continue
            suggestion_id_value = action_payload.get("source_suggestion_id")
            current = collapsed.get(str(suggestion_id_value))
            if current is None:
                continue
            collapsed[str(suggestion_id_value)] = apply_case_promotion(
                current,
                action_payload,
                timeline_projection.source_event_id,
            )

    items = list(collapsed.values())
    items.sort(key=lambda item: item.promoted_at or item.resolved_at or item.event_ts, reverse=True)
    filtered = []
    for item in items:
        if status and item.status != status:
            continue
        if suggestion_type and item.suggestion_type != suggestion_type:
            continue
        if camera_id and item.camera_id != camera_id:
            continue
        if subject_id and item.subject_id != subject_id:
            continue
        filtered.append(item)
    if not settings.include_fixture_projections_when_live:
        filtered = remove_fixture_only_items_when_live(filtered, live_evidence_exists=timeline_has_live_evidence(session))
    filtered = apply_live_first_order(filtered)
    return filtered[safe_offset : safe_offset + safe_limit]


def get_case_suggestion(session: Session, suggestion_id: str) -> CaseSuggestionRead | None:
    for item in list_case_suggestions(session, limit=get_settings().max_query_limit):
        if item.suggestion_id == suggestion_id:
            return item
    return None


def resolve_case_suggestion(
    session: Session,
    suggestion_id: str,
    request: CaseSuggestionResolutionRequest,
) -> CaseSuggestionRead:
    current = get_case_suggestion(session, suggestion_id)
    if current is None:
        raise WorkflowNotFoundError("Case suggestion not found")

    if current.promoted_case_id and request.decision != "accepted":
        raise WorkflowConflictError("Promoted case suggestions cannot be changed away from accepted")

    if current.status in FINAL_CASE_SUGGESTION_STATUSES and not _same_case_suggestion_resolution(current, request):
        raise WorkflowConflictError("Case suggestion already resolved with a final decision")

    if current.status in FINAL_CASE_SUGGESTION_STATUSES and _same_case_suggestion_resolution(current, request):
        return current

    resolved_by = (request.resolved_by or "").strip()
    if not resolved_by:
        raise WorkflowValidationError("resolved_by is required")

    resolved_at = datetime.now(timezone.utc)
    action_payload = {
        "suggestion_id": current.suggestion_id,
        "source_event_id": current.source_event_id,
        "source_event_type": current.source_event_type,
        "suggestion_type": current.suggestion_type,
        "decision": request.decision,
        "decision_reason": request.decision_reason,
        "resolved_by": resolved_by,
        "resolved_by_user_id": request.resolved_by_user_id,
        "resolved_at": resolved_at.isoformat(),
        "resolution_payload": dict(request.resolution_payload),
    }
    action_key = normalized_workflow_payload(
        {
            "suggestion_id": suggestion_id,
            "action_type": "case_suggestion_resolved",
            "request": request.model_dump(mode="json"),
            "resolved_by": resolved_by,
            "resolved_by_user_id": request.resolved_by_user_id,
        }
    )
    _, created = create_audit_timeline_event(
        session,
        event_type="case_suggestion_resolved",
        action_key=action_key,
        summary=f"Sugerencia de caso resuelta como {request.decision}",
        severity=str(current.payload.get("severity", "medium")),
        payload={"case_suggestion_resolution": action_payload},
        occurred_at=resolved_at,
        camera_id=current.camera_id,
        subject_id=current.subject_id,
        track_id=current.track_id,
        organization_id=current.organization_id,
        site_id=current.site_id,
        confidence=action_confidence(current.payload),
    )
    if created:
        session.commit()
    else:
        session.rollback()

    updated = get_case_suggestion(session, suggestion_id)
    if updated is None:
        raise WorkflowNotFoundError("Case suggestion disappeared after resolution")
    return updated


def promote_case_suggestion(
    session: Session,
    suggestion_id: str,
    request: PromoteCaseSuggestionRequest,
) -> CaseRecordRead:
    suggestion = get_case_suggestion(session, suggestion_id)
    if suggestion is None:
        raise WorkflowNotFoundError("Case suggestion not found")

    if suggestion.status != "accepted" and suggestion.promoted_case_id is None:
        raise WorkflowConflictError("Case suggestion must be accepted before promotion")

    resolved_by = (request.resolved_by or "").strip()
    if not resolved_by:
        raise WorkflowValidationError("resolved_by is required")

    case_record, _ = create_case_from_suggestion(session, suggestion=suggestion, request=request)
    promoted_at = datetime.now(timezone.utc)
    action_payload = {
        "case_id": case_record.case_id,
        "case_code": case_record.case_code,
        "case_type": case_record.case_type,
        "title": case_record.title,
        "priority": case_record.priority,
        "severity": case_record.severity,
        "source_suggestion_id": suggestion.suggestion_id,
        "source_event_id": suggestion.source_event_id,
        "resolved_by": resolved_by,
        "resolved_by_user_id": request.resolved_by_user_id,
        "promoted_at": promoted_at.isoformat(),
        "case_payload": case_record.case_payload,
    }
    action_key = normalized_workflow_payload(
        {
            "suggestion_id": suggestion.suggestion_id,
            "action_type": "case_record_created",
        }
    )
    _, created = create_audit_timeline_event(
        session,
        event_type="case_record_created",
        action_key=action_key,
        summary=f"Caso canónico creado desde suggestion {suggestion.suggestion_id}",
        severity=case_record.severity,
        payload={"case_record_created": action_payload},
        occurred_at=promoted_at,
        camera_id=suggestion.camera_id,
        subject_id=suggestion.subject_id,
        track_id=suggestion.track_id,
        organization_id=suggestion.organization_id,
        site_id=suggestion.site_id,
        case_id=case_record.case_id,
        confidence=action_confidence(suggestion.payload),
    )
    if created:
        session.commit()
    else:
        session.rollback()

    return case_record


def read_action_timeline_projection(row: TimelineEvent):
    from app.services.events import read_timeline_record

    return read_timeline_record(row)


def _same_case_suggestion_resolution(
    current: CaseSuggestionRead,
    request: CaseSuggestionResolutionRequest,
) -> bool:
    return (
        current.decision == request.decision
        and current.decision_reason == request.decision_reason
        and current.resolved_by == request.resolved_by
        and dict(current.resolution_payload or {}) == dict(request.resolution_payload or {})
    )
