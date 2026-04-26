from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TimelineEvent
from app.services.events import (
    MANUAL_REVIEW_EVENT_TYPES,
    ManualReviewRead,
    read_manual_review_record,
)
from app.services.timeline_service import (
    action_confidence,
    apply_manual_review_resolution,
    create_audit_timeline_event,
    list_timeline_rows,
    normalized_workflow_payload,
)
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError


MANUAL_REVIEW_ACTION_EVENT_TYPES = {"manual_review_resolved", "identity_conflict_resolved"}
MANUAL_REVIEW_RELEVANT_EVENT_TYPES = MANUAL_REVIEW_EVENT_TYPES | MANUAL_REVIEW_ACTION_EVENT_TYPES
FINAL_MANUAL_REVIEW_STATUSES = {"approved", "rejected"}


class ManualReviewResolutionRequest(BaseModel):
    decision: Literal["approved", "rejected", "needs_more_evidence"]
    decision_reason: str
    resolved_by: str | None = None
    resolved_by_user_id: str | None = None
    identity_resolution: Literal["confirm_identity", "discard_candidate", "mark_unresolved", "escalate"] | None = None
    confirmed_person_profile_id: str | None = None
    discarded_person_profile_id: str | None = None
    resolution_payload: dict[str, Any] = Field(default_factory=dict)


def list_manual_reviews(
    session: Session,
    *,
    limit: int,
    offset: int = 0,
    status: str | None = None,
    review_type: str | None = None,
    priority: int | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
) -> list[ManualReviewRead]:
    settings = get_settings()
    safe_limit = max(1, min(limit, settings.max_query_limit))
    safe_offset = max(0, offset)
    base_rows = list_timeline_rows(
        session,
        event_types=MANUAL_REVIEW_EVENT_TYPES,
        descending=False,
    )
    action_rows = list_timeline_rows(
        session,
        event_types=MANUAL_REVIEW_ACTION_EVENT_TYPES,
        descending=False,
    )

    collapsed: dict[str, ManualReviewRead] = {}
    for row in base_rows:
        projection = read_manual_review_record(row)
        if projection is None:
            continue
        existing = collapsed.get(projection.review_id)
        if existing is None or projection.event_ts >= existing.event_ts:
            collapsed[projection.review_id] = projection

    for row in action_rows:
        action_payload = (row.payload or {}).get("manual_review_resolution")
        if not isinstance(action_payload, dict):
            continue
        review_id_value = action_payload.get("review_id")
        if review_id_value is None:
            continue
        current = collapsed.get(str(review_id_value))
        if current is None:
            continue
        action_timeline = read_action_timeline_projection(row)
        collapsed[str(review_id_value)] = apply_manual_review_resolution(
            current,
            action_payload,
            action_timeline.source_event_id,
        )

    items = list(collapsed.values())
    items.sort(key=lambda item: item.resolved_at or item.event_ts, reverse=True)
    filtered = []
    for item in items:
        if status and item.status != status:
            continue
        if review_type and item.review_type != review_type:
            continue
        if priority is not None and item.priority != priority:
            continue
        if camera_id and item.camera_id != camera_id:
            continue
        if subject_id and item.subject_id != subject_id:
            continue
        filtered.append(item)
    return filtered[safe_offset : safe_offset + safe_limit]


def get_manual_review(session: Session, review_id: str) -> ManualReviewRead | None:
    for item in list_manual_reviews(session, limit=get_settings().max_query_limit):
        if item.review_id == review_id:
            return item
    return None


def resolve_manual_review(
    session: Session,
    review_id: str,
    request: ManualReviewResolutionRequest,
) -> ManualReviewRead:
    current = get_manual_review(session, review_id)
    if current is None:
        raise WorkflowNotFoundError("Manual review not found")

    if current.review_type == "identity_conflict" and request.identity_resolution is None:
        raise WorkflowValidationError("identity_resolution is required for identity_conflict reviews")

    if current.review_type != "identity_conflict" and request.identity_resolution is not None:
        raise WorkflowValidationError("identity_resolution is only valid for identity_conflict reviews")

    if current.status in FINAL_MANUAL_REVIEW_STATUSES and not _same_manual_review_resolution(current, request):
        raise WorkflowConflictError("Manual review already resolved with a final decision")

    if current.status in FINAL_MANUAL_REVIEW_STATUSES and _same_manual_review_resolution(current, request):
        return current

    resolved_by = (request.resolved_by or "").strip()
    if not resolved_by:
        raise WorkflowValidationError("resolved_by is required")

    resolved_at = datetime.now(timezone.utc)
    resolution_payload = {
        **dict(request.resolution_payload),
        **(
            {
                "identity_resolution": request.identity_resolution,
                "confirmed_person_profile_id": request.confirmed_person_profile_id,
                "discarded_person_profile_id": request.discarded_person_profile_id,
            }
            if current.review_type == "identity_conflict"
            else {}
        ),
    }
    action_payload = {
        "review_id": current.review_id,
        "source_event_id": current.source_event_id,
        "source_event_type": current.source_event_type,
        "review_type": current.review_type,
        "decision": request.decision,
        "decision_reason": request.decision_reason,
        "resolved_by": resolved_by,
        "resolved_by_user_id": request.resolved_by_user_id,
        "resolved_at": resolved_at.isoformat(),
        "resolution_payload": resolution_payload,
    }
    action_type = "identity_conflict_resolved" if current.review_type == "identity_conflict" else "manual_review_resolved"
    action_key = normalized_workflow_payload(
        {
        "review_id": review_id,
        "action_type": action_type,
        "request": request.model_dump(mode="json"),
        "resolved_by": resolved_by,
        "resolved_by_user_id": request.resolved_by_user_id,
    }
    )
    summary = (
        f"Conflicto de identidad resuelto como {request.decision}"
        if current.review_type == "identity_conflict"
        else f"Revisión manual resuelta como {request.decision}"
    )
    _, created = create_audit_timeline_event(
        session,
        event_type=action_type,
        action_key=action_key,
        summary=summary,
        severity=current.severity,
        payload={"manual_review_resolution": action_payload},
        occurred_at=resolved_at,
        camera_id=current.camera_id,
        subject_id=current.subject_id,
        track_id=current.track_id,
        organization_id=current.organization_id,
        site_id=current.site_id,
        person_profile_id=request.confirmed_person_profile_id if request.identity_resolution == "confirm_identity" else None,
        confidence=action_confidence(current.payload),
    )
    if created:
        session.commit()
    else:
        session.rollback()

    updated = get_manual_review(session, review_id)
    if updated is None:
        raise WorkflowNotFoundError("Manual review disappeared after resolution")
    return updated


def read_action_timeline_projection(row: TimelineEvent):
    from app.services.events import read_timeline_record

    return read_timeline_record(row)


def _same_manual_review_resolution(current: ManualReviewRead, request: ManualReviewResolutionRequest) -> bool:
    return (
        current.decision == request.decision
        and current.decision_reason == request.decision_reason
        and current.resolved_by == request.resolved_by
        and dict(current.resolution_payload or {}) == _normalized_resolution_payload(current.review_type, request)
    )


def _normalized_resolution_payload(review_type: str, request: ManualReviewResolutionRequest) -> dict[str, Any]:
    payload = dict(request.resolution_payload or {})
    if review_type == "identity_conflict":
        payload.update(
            {
                "identity_resolution": request.identity_resolution,
                "confirmed_person_profile_id": request.confirmed_person_profile_id,
                "discarded_person_profile_id": request.discarded_person_profile_id,
            }
        )
    return payload
