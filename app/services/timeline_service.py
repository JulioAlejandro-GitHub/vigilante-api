from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Camera, CaseRecord, Organization, PersonProfile, Site, TimelineEvent
from app.services.events import (
    TimelineEventRead,
    _as_float,
    as_str,
    build_storage_event_uuid,
    parse_uuid,
    read_timeline_record,
    resolve_existing_uuid,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def list_timeline_rows(
    session: Session,
    *,
    event_types: set[str] | None = None,
    descending: bool = True,
    limit: int | None = None,
) -> list[TimelineEvent]:
    stmt = select(TimelineEvent)
    if event_types:
        stmt = stmt.where(TimelineEvent.event_type.in_(sorted(event_types)))
    stmt = stmt.order_by(TimelineEvent.occurred_at.desc() if descending else TimelineEvent.occurred_at.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def build_action_source_event_id(event_type: str, action_key: str) -> str:
    normalized_key = json.dumps({"event_type": event_type, "action_key": action_key}, sort_keys=True, ensure_ascii=True)
    suffix = build_storage_event_uuid("vigilante-api/action-key", normalized_key)
    return f"evt_api_{event_type}_{str(suffix).replace('-', '')[:16]}"


def create_audit_timeline_event(
    session: Session,
    *,
    event_type: str,
    action_key: str,
    summary: str,
    severity: str,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    track_id: str | None = None,
    organization_id: str | None = None,
    site_id: str | None = None,
    person_profile_id: str | None = None,
    case_id: str | None = None,
    confidence: float | None = None,
) -> tuple[TimelineEventRead, bool]:
    settings = get_settings()
    event_ts = occurred_at or utcnow()
    source_component = settings.workflow_source_component
    source_event_id = build_action_source_event_id(event_type, action_key)
    storage_source_event_id = build_storage_event_uuid(source_component, source_event_id)

    existing = session.scalar(
        select(TimelineEvent).where(
            TimelineEvent.source_component == source_component,
            TimelineEvent.source_event_id == storage_source_event_id,
        )
    )
    if existing is not None:
        return read_timeline_record(existing), False

    timeline_projection = TimelineEventRead(
        source_event_id=source_event_id,
        event_type=event_type,
        event_ts=event_ts,
        camera_id=camera_id,
        subject_id=subject_id,
        track_id=track_id,
        severity=severity,
        confidence=confidence,
        summary=summary,
        payload=dict(payload),
        source_component=source_component,
        organization_id=organization_id,
        site_id=site_id,
    )

    record = TimelineEvent(
        source_component=source_component,
        source_event_id=storage_source_event_id,
        event_type=event_type,
        occurred_at=event_ts,
        organization_id=resolve_existing_uuid(session, Organization, organization_id),
        site_id=resolve_existing_uuid(session, Site, site_id),
        camera_id=resolve_existing_uuid(session, Camera, camera_id),
        observed_subject_id=parse_uuid(subject_id),
        person_profile_id=resolve_existing_uuid(session, PersonProfile, person_profile_id),
        case_id=resolve_existing_uuid(session, CaseRecord, case_id),
        severity=severity,
        summary=summary,
        payload={
            **payload,
            "timeline_projection": timeline_projection.model_dump(mode="json"),
            "action_event": {
                "event_id": source_event_id,
                "event_type": event_type,
                "occurred_at": event_ts.isoformat(),
                "source_component": source_component,
                "action_key": action_key,
                "context": {
                    "camera_id": camera_id,
                    "subject_id": subject_id,
                    "track_id": track_id,
                    "organization_id": organization_id,
                    "site_id": site_id,
                    "person_profile_id": person_profile_id,
                    "case_id": case_id,
                },
                "payload": payload,
            },
            "storage_source_event_id": str(storage_source_event_id),
        },
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(TimelineEvent).where(
                TimelineEvent.source_component == source_component,
                TimelineEvent.source_event_id == storage_source_event_id,
            )
        )
        if existing is None:
            raise
        return read_timeline_record(existing), False
    return timeline_projection, True


def apply_manual_review_resolution(
    current,
    action_payload: dict[str, Any],
    resolution_event_id: str,
):
    updated = current.model_copy(deep=True)
    updated.status = str(action_payload["decision"])
    updated.decision = str(action_payload["decision"])
    updated.decision_reason = as_str(action_payload.get("decision_reason"))
    updated.resolved_by = as_str(action_payload.get("resolved_by"))
    resolved_at = action_payload.get("resolved_at")
    updated.resolved_at = datetime.fromisoformat(resolved_at) if resolved_at else None
    updated.resolution_payload = dict(action_payload.get("resolution_payload") or {})
    updated.resolution_event_id = resolution_event_id
    return updated


def apply_case_suggestion_resolution(
    current,
    action_payload: dict[str, Any],
    resolution_event_id: str,
):
    updated = current.model_copy(deep=True)
    updated.status = str(action_payload["decision"])
    updated.decision = str(action_payload["decision"])
    updated.decision_reason = as_str(action_payload.get("decision_reason"))
    updated.resolved_by = as_str(action_payload.get("resolved_by"))
    resolved_at = action_payload.get("resolved_at")
    updated.resolved_at = datetime.fromisoformat(resolved_at) if resolved_at else None
    updated.resolution_payload = dict(action_payload.get("resolution_payload") or {})
    updated.resolution_event_id = resolution_event_id
    return updated


def apply_case_promotion(current, promotion_payload: dict[str, Any], promotion_event_id: str):
    updated = current.model_copy(deep=True)
    updated.status = "accepted"
    updated.decision = updated.decision or "accepted"
    updated.promoted_case_id = as_str(promotion_payload.get("case_id"))
    promoted_at = promotion_payload.get("promoted_at")
    updated.promoted_at = datetime.fromisoformat(promoted_at) if promoted_at else None
    if updated.resolution_event_id is None:
        updated.resolution_event_id = promotion_event_id
    if updated.resolved_at is None and updated.promoted_at is not None:
        updated.resolved_at = updated.promoted_at
    return updated


def normalized_workflow_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def timeline_projection_payload(record: TimelineEvent) -> dict[str, Any]:
    projection = read_timeline_record(record)
    return projection.model_dump(mode="json")


def action_confidence(payload: dict[str, Any]) -> float | None:
    return _as_float(payload.get("confidence"))
