from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Camera, CaseItem, CaseRecord, Organization, PersonProfile, Site, Zone
from app.services.events import as_str, build_projection_uuid, parse_uuid, resolve_existing_uuid
from app.services.workflow_exceptions import WorkflowNotFoundError, WorkflowValidationError


DB_CASE_TYPES = {
    "human_presence",
    "unknown_face",
    "candidate_match_conflict",
    "watchlist_investigation",
    "manual_investigation",
    "loitering",
    "multi_event_tracking",
}
CASE_TYPE_MAPPING = {
    "unresolved_subject_case": "multi_event_tracking",
    "identity_conflict": "candidate_match_conflict",
    "manual_review": "manual_investigation",
}
CASE_PRIORITY_MAPPING = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "normal": 3,
}
CASE_SEVERITIES = {"low", "medium", "high", "critical"}


class PromoteCaseSuggestionRequest(BaseModel):
    resolved_by: str
    case_type: str
    title: str
    priority: int | str = "medium"
    severity: str = "medium"
    description: str | None = None
    case_payload: dict[str, Any] = Field(default_factory=dict)


class CaseRecordRead(BaseModel):
    case_id: str
    case_code: str
    case_type: str
    title: str
    status: str
    priority: int
    severity: str
    source_suggestion_id: str | None = None
    source_event_id: str | None = None
    primary_subject_id: str | None = None
    primary_camera_id: str | None = None
    opened_at: datetime
    organization_id: str | None = None
    site_id: str | None = None
    case_payload: dict[str, Any] = Field(default_factory=dict)


def build_case_id(source_suggestion_id: str) -> UUID:
    return build_projection_uuid("case-record", source_suggestion_id)


def build_case_code(case_id: UUID) -> str:
    return f"CASE-{str(case_id).replace('-', '').upper()[:12]}"


def normalize_case_type(case_type: str) -> str:
    if case_type in DB_CASE_TYPES:
        return case_type
    mapped = CASE_TYPE_MAPPING.get(case_type)
    if mapped is None:
        raise WorkflowValidationError(f"Unsupported case_type for current schema: {case_type}")
    return mapped


def normalize_case_priority(priority: int | str) -> int:
    if isinstance(priority, int):
        if 1 <= priority <= 5:
            return priority
        raise WorkflowValidationError("priority must be between 1 and 5")
    mapped = CASE_PRIORITY_MAPPING.get(str(priority).lower())
    if mapped is None:
        raise WorkflowValidationError(f"Unsupported priority value: {priority}")
    return mapped


def normalize_case_severity(severity: str) -> str:
    normalized = severity.lower()
    if normalized not in CASE_SEVERITIES:
        raise WorkflowValidationError(f"Unsupported severity value: {severity}")
    return normalized


def create_case_from_suggestion(
    session: Session,
    *,
    suggestion,
    request: PromoteCaseSuggestionRequest,
) -> tuple[CaseRecordRead, bool]:
    case_id = build_case_id(suggestion.suggestion_id)
    existing = session.get(CaseRecord, case_id)
    if existing is not None:
        return read_case_record(existing), False

    db_case_type = normalize_case_type(request.case_type)
    priority_value = normalize_case_priority(request.priority)
    severity_value = normalize_case_severity(request.severity)
    opened_at = datetime.now(timezone.utc)
    raw_zone_id = suggestion.payload.get("zone_id")
    raw_person_profile_id = suggestion.payload.get("person_profile_id")

    record = CaseRecord(
        case_id=case_id,
        case_code=build_case_code(case_id),
        case_type=db_case_type,
        case_status="open",
        source_type="hybrid",
        priority=priority_value,
        severity=severity_value,
        title=request.title,
        description=request.description or suggestion.payload.get("suggested_reason") or suggestion.reason_summary,
        primary_camera_id=resolve_existing_uuid(session, Camera, suggestion.camera_id),
        primary_observed_subject_id=parse_uuid(suggestion.subject_id),
        primary_person_profile_id=resolve_existing_uuid(session, PersonProfile, raw_person_profile_id),
        opened_at=opened_at,
        created_by_type="human",
        case_metadata={
            "source_suggestion_id": suggestion.suggestion_id,
            "source_event_id": suggestion.source_event_id,
            "source_suggestion_type": suggestion.suggestion_type,
            "requested_case_type": request.case_type,
            "db_case_type": db_case_type,
            "resolved_by": request.resolved_by,
            "source_case_suggestion_payload": suggestion.payload,
            "case_payload": dict(request.case_payload),
            "raw_primary_camera_id": suggestion.camera_id,
            "raw_primary_subject_id": suggestion.subject_id,
            "raw_organization_id": suggestion.organization_id,
            "raw_site_id": suggestion.site_id,
            "raw_zone_id": as_str(raw_zone_id),
            "raw_person_profile_id": as_str(raw_person_profile_id),
        },
        organization_id=resolve_existing_uuid(session, Organization, suggestion.organization_id),
        site_id=resolve_existing_uuid(session, Site, suggestion.site_id),
        zone_id=resolve_existing_uuid(session, Zone, raw_zone_id),
    )
    session.add(record)
    session.flush()

    session.add(
        CaseItem(
            case_id=case_id,
            item_type="external_reference",
            item_ref_text=f"source_suggestion_id:{suggestion.suggestion_id}",
            is_primary=False,
            note="Slice 2 source suggestion",
        )
    )
    subject_uuid = parse_uuid(suggestion.subject_id)
    if subject_uuid is not None:
        session.add(
            CaseItem(
                case_id=case_id,
                item_type="observed_subject",
                item_ref_uuid=subject_uuid,
                is_primary=True,
                note="Primary observed subject from accepted case suggestion",
            )
        )

    return read_case_record(record), True


def list_cases(session: Session, *, limit: int) -> list[CaseRecordRead]:
    settings = get_settings()
    safe_limit = max(1, min(limit, settings.max_query_limit))
    rows = session.scalars(select(CaseRecord).order_by(CaseRecord.opened_at.desc()).limit(safe_limit)).all()
    return [read_case_record(row) for row in rows]


def get_case(session: Session, case_id: str) -> CaseRecordRead:
    record = session.get(CaseRecord, parse_uuid(case_id))
    if record is None:
        raise WorkflowNotFoundError("Case record not found")
    return read_case_record(record)


def read_case_record(record: CaseRecord) -> CaseRecordRead:
    metadata = dict(record.case_metadata or {})
    return CaseRecordRead(
        case_id=str(record.case_id),
        case_code=record.case_code,
        case_type=record.case_type,
        title=record.title,
        status=record.case_status,
        priority=int(record.priority),
        severity=record.severity,
        source_suggestion_id=as_str(metadata.get("source_suggestion_id")),
        source_event_id=as_str(metadata.get("source_event_id")),
        primary_subject_id=as_str(record.primary_observed_subject_id),
        primary_camera_id=as_str(record.primary_camera_id),
        opened_at=record.opened_at,
        organization_id=as_str(record.organization_id),
        site_id=as_str(record.site_id),
        case_payload=metadata,
    )
