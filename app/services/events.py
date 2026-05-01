from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Camera, Organization, PersonProfile, Site, TimelineEvent
from app.services.live_first_read_service import apply_live_first_order
from app.services.media_models import EvidenceMediaItem


SUPPORTED_EVENT_TYPES = {
    "human_presence_no_face",
    "face_detected_unidentified",
    "face_detected_identified",
    "manual_review_required",
    "identity_conflict",
    "recurrent_unresolved_subject",
    "case_suggestion_created",
}
MANUAL_REVIEW_EVENT_TYPES = {"manual_review_required", "identity_conflict"}
CASE_SUGGESTION_EVENT_TYPES = {"case_suggestion_created"}
PROJECTION_NAMESPACE = uuid5(UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8"), "vigilante-api/projections")


class EventSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    component: str | None = None
    instance: str | None = None
    version: str | None = None


class RecognitionEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str
    event_type: str
    event_version: str
    occurred_at: datetime
    emitted_at: datetime | None = None
    source: EventSource | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class TimelineEventRead(BaseModel):
    source_event_id: str
    event_type: str
    event_ts: datetime
    case_id: str | None = None
    camera_id: str | None = None
    subject_id: str | None = None
    track_id: str | None = None
    severity: str
    confidence: float | None = None
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source_component: str
    organization_id: str | None = None
    site_id: str | None = None
    evidence_media: list[EvidenceMediaItem] = Field(default_factory=list)


class ManualReviewRead(BaseModel):
    review_id: str
    source_event_id: str
    source_event_type: str
    review_type: str
    status: str
    priority: int
    severity: str
    subject_id: str | None = None
    track_id: str | None = None
    camera_id: str | None = None
    organization_id: str | None = None
    site_id: str | None = None
    event_ts: datetime
    reason_summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None
    decision_reason: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_payload: dict[str, Any] = Field(default_factory=dict)
    resolution_event_id: str | None = None
    evidence_media: list[EvidenceMediaItem] = Field(default_factory=list)


class CaseSuggestionRead(BaseModel):
    suggestion_id: str
    source_event_id: str
    source_event_type: str
    suggestion_type: str
    status: str
    subject_id: str | None = None
    track_id: str | None = None
    camera_id: str | None = None
    organization_id: str | None = None
    site_id: str | None = None
    event_ts: datetime
    evidence_count: int
    reason_summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None
    decision_reason: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_payload: dict[str, Any] = Field(default_factory=dict)
    resolution_event_id: str | None = None
    promoted_case_id: str | None = None
    promoted_at: datetime | None = None
    evidence_media: list[EvidenceMediaItem] = Field(default_factory=list)


class IngestionResult(BaseModel):
    status: Literal["applied", "duplicate"]
    source_event_id: str
    timeline: TimelineEventRead
    manual_review: ManualReviewRead | None = None
    case_suggestion: CaseSuggestionRead | None = None


def build_storage_event_uuid(source_component: str, source_event_id: str) -> UUID:
    return uuid5(PROJECTION_NAMESPACE, f"source-event:{source_component}:{source_event_id}")


def build_projection_uuid(kind: str, *parts: str | None) -> UUID:
    normalized = "|".join(part or "" for part in parts)
    return uuid5(PROJECTION_NAMESPACE, f"{kind}:{normalized}")


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_uuid(value: Any) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def as_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def resolve_existing_uuid(session: Session, model, raw_value: Any):
    candidate = parse_uuid(raw_value)
    if candidate is None:
        return None
    return candidate if session.get(model, candidate) is not None else None


def severity_to_priority(severity: str) -> int:
    return {
        "critical": 1,
        "high": 2,
        "medium": 3,
        "low": 4,
    }.get(severity, 4)


def build_reason_summary(event: RecognitionEventEnvelope) -> str:
    decision_reason = event.payload.get("decision_reason", [])
    if isinstance(decision_reason, list) and decision_reason:
        return ", ".join(str(item) for item in decision_reason[:3])
    review_type = event.payload.get("review_type")
    if review_type:
        return f"review_type={review_type}"
    return {
        "human_presence_no_face": "human_presence_without_usable_face",
        "face_detected_unidentified": "usable_face_without_confident_match",
        "face_detected_identified": "known_identity_resolved",
        "manual_review_required": "manual_resolution_required",
        "identity_conflict": "identity_conflict_detected",
        "recurrent_unresolved_subject": "recurrent_unresolved_subject_detected",
        "case_suggestion_created": "case_suggestion_threshold_passed",
    }.get(event.event_type, event.event_type)


def build_event_summary(event: RecognitionEventEnvelope) -> str:
    matched_person = event.payload.get("matched_person") or {}
    full_name = matched_person.get("full_name")
    if event.event_type == "face_detected_identified" and full_name:
        return f"Rostro identificado como {full_name}"
    return {
        "human_presence_no_face": "Presencia humana detectada sin rostro utilizable",
        "face_detected_unidentified": "Rostro utilizable detectado sin identificación confiable",
        "face_detected_identified": "Rostro identificado con match confiable",
        "manual_review_required": "Evento elevado a revisión manual",
        "identity_conflict": "Conflicto de identidad detectado entre señales técnicas",
        "recurrent_unresolved_subject": "Recurrencia de sujeto no resuelto detectada",
        "case_suggestion_created": "Sugerencia de caso creada por acumulación de evidencia",
    }.get(event.event_type, f"Evento {event.event_type}")


def build_timeline_projection(event: RecognitionEventEnvelope, *, source_component: str) -> TimelineEventRead:
    return TimelineEventRead(
        source_event_id=event.event_id,
        event_type=event.event_type,
        event_ts=ensure_aware(event.occurred_at),
        camera_id=as_str(event.context.get("camera_id")),
        subject_id=as_str(event.context.get("subject_id")),
        track_id=as_str(event.context.get("track_id")),
        severity=str(event.payload.get("severity", "low")),
        confidence=_as_float(event.payload.get("confidence")),
        summary=build_event_summary(event),
        payload=dict(event.payload),
        source_component=source_component,
        organization_id=as_str(event.context.get("organization_id")),
        site_id=as_str(event.context.get("site_id")),
    )


def build_manual_review_projection(event: RecognitionEventEnvelope) -> ManualReviewRead | None:
    if event.event_type not in MANUAL_REVIEW_EVENT_TYPES:
        return None
    review_type = str(event.payload.get("review_type") or ("identity_conflict" if event.event_type == "identity_conflict" else "manual_review_required"))
    evidence_refs = event.payload.get("evidence_refs") if isinstance(event.payload.get("evidence_refs"), list) else []
    review_id = build_projection_uuid(
        "manual-review",
        review_type,
        as_str(event.context.get("track_id")),
        as_str(event.context.get("subject_id")),
        as_str(event.context.get("camera_id")),
        as_str(evidence_refs[0] if evidence_refs else None),
    )
    return ManualReviewRead(
        review_id=str(review_id),
        source_event_id=event.event_id,
        source_event_type=event.event_type,
        review_type=review_type,
        status="pending",
        priority=severity_to_priority(str(event.payload.get("severity", "medium"))),
        severity=str(event.payload.get("severity", "medium")),
        subject_id=as_str(event.context.get("subject_id")),
        track_id=as_str(event.context.get("track_id")),
        camera_id=as_str(event.context.get("camera_id")),
        organization_id=as_str(event.context.get("organization_id")),
        site_id=as_str(event.context.get("site_id")),
        event_ts=ensure_aware(event.occurred_at),
        reason_summary=build_reason_summary(event),
        payload=dict(event.payload),
    )


def build_case_suggestion_projection(event: RecognitionEventEnvelope) -> CaseSuggestionRead | None:
    if event.event_type not in CASE_SUGGESTION_EVENT_TYPES:
        return None
    evidence_refs = event.payload.get("evidence_refs") if isinstance(event.payload.get("evidence_refs"), list) else []
    suggestion_type = str(event.payload.get("suggestion_type") or "unresolved_subject_case")
    suggestion_id = build_projection_uuid(
        "case-suggestion",
        suggestion_type,
        as_str(event.context.get("track_id")),
        as_str(event.context.get("subject_id")),
        as_str(event.context.get("camera_id")),
        as_str(evidence_refs[0] if evidence_refs else None),
    )
    return CaseSuggestionRead(
        suggestion_id=str(suggestion_id),
        source_event_id=event.event_id,
        source_event_type=event.event_type,
        suggestion_type=suggestion_type,
        status="pending",
        subject_id=as_str(event.context.get("subject_id")),
        track_id=as_str(event.context.get("track_id")),
        camera_id=as_str(event.context.get("camera_id")),
        organization_id=as_str(event.context.get("organization_id")),
        site_id=as_str(event.context.get("site_id")),
        event_ts=ensure_aware(event.occurred_at),
        evidence_count=int(event.payload.get("evidence_count") or 0),
        reason_summary=build_reason_summary(event),
        payload=dict(event.payload),
    )


def ingest_event(session: Session, event: RecognitionEventEnvelope) -> IngestionResult:
    if event.event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(f"Unsupported recognition event type: {event.event_type}")

    settings = get_settings()
    source_component = (event.source.component if event.source else None) or settings.default_source_component
    storage_source_event_id = build_storage_event_uuid(source_component, event.event_id)
    timeline_projection = build_timeline_projection(event, source_component=source_component)
    manual_review_projection = build_manual_review_projection(event)
    case_suggestion_projection = build_case_suggestion_projection(event)

    existing = session.scalar(
        select(TimelineEvent).where(
            TimelineEvent.source_component == source_component,
            TimelineEvent.source_event_id == storage_source_event_id,
        )
    )
    if existing is not None:
        if _backfill_existing_projection_scope(
            session,
            existing,
            timeline_projection=timeline_projection,
            manual_review_projection=manual_review_projection,
            case_suggestion_projection=case_suggestion_projection,
        ):
            session.commit()
            session.refresh(existing)
        return build_ingestion_result(existing, status="duplicate")

    record = TimelineEvent(
        source_component=source_component,
        source_event_id=storage_source_event_id,
        event_type=event.event_type,
        occurred_at=ensure_aware(event.occurred_at),
        organization_id=resolve_existing_uuid(session, Organization, event.context.get("organization_id")),
        site_id=resolve_existing_uuid(session, Site, event.context.get("site_id")),
        camera_id=resolve_existing_uuid(session, Camera, event.context.get("camera_id")),
        observed_subject_id=parse_uuid(event.context.get("subject_id")),
        person_profile_id=resolve_existing_uuid(session, PersonProfile, event.payload.get("person_profile_id")),
        severity=timeline_projection.severity,
        summary=timeline_projection.summary,
        payload={
            "source_event": event.model_dump(mode="json"),
            "timeline_projection": timeline_projection.model_dump(mode="json"),
            "manual_review_projection": manual_review_projection.model_dump(mode="json") if manual_review_projection else None,
            "case_suggestion_projection": case_suggestion_projection.model_dump(mode="json") if case_suggestion_projection else None,
            "storage_source_event_id": str(storage_source_event_id),
        },
    )
    session.add(record)
    try:
        session.commit()
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
        return build_ingestion_result(existing, status="duplicate")

    session.refresh(record)
    return build_ingestion_result(record, status="applied")


def _backfill_existing_projection_scope(
    session: Session,
    record: TimelineEvent,
    *,
    timeline_projection: TimelineEventRead,
    manual_review_projection: ManualReviewRead | None,
    case_suggestion_projection: CaseSuggestionRead | None,
) -> bool:
    payload = dict(record.payload or {})
    changed = False
    changed = _backfill_projection_payload(payload, "timeline_projection", timeline_projection) or changed
    if manual_review_projection is not None:
        changed = _backfill_projection_payload(payload, "manual_review_projection", manual_review_projection) or changed
    if case_suggestion_projection is not None:
        changed = _backfill_projection_payload(payload, "case_suggestion_projection", case_suggestion_projection) or changed

    if record.organization_id is None and timeline_projection.organization_id:
        resolved = resolve_existing_uuid(session, Organization, timeline_projection.organization_id)
        if resolved is not None:
            record.organization_id = resolved
            changed = True
    if record.site_id is None and timeline_projection.site_id:
        resolved = resolve_existing_uuid(session, Site, timeline_projection.site_id)
        if resolved is not None:
            record.site_id = resolved
            changed = True
    if record.camera_id is None and timeline_projection.camera_id:
        resolved = resolve_existing_uuid(session, Camera, timeline_projection.camera_id)
        if resolved is not None:
            record.camera_id = resolved
            changed = True

    if changed:
        record.payload = payload
    return changed


def _backfill_projection_payload(payload: dict[str, Any], key: str, projection: BaseModel) -> bool:
    stored = payload.get(key)
    if not isinstance(stored, dict):
        return False
    updated = dict(stored)
    changed = False
    for field_name in ("organization_id", "site_id", "camera_id"):
        value = getattr(projection, field_name, None)
        if value and not updated.get(field_name):
            updated[field_name] = value
            changed = True
    if changed:
        payload[key] = updated
    return changed


def build_ingestion_result(record: TimelineEvent, *, status: Literal["applied", "duplicate"]) -> IngestionResult:
    timeline = read_timeline_record(record)
    return IngestionResult(
        status=status,
        source_event_id=timeline.source_event_id,
        timeline=timeline,
        manual_review=read_manual_review_record(record),
        case_suggestion=read_case_suggestion_record(record),
    )


def read_timeline_record(record: TimelineEvent) -> TimelineEventRead:
    stored = record.payload.get("timeline_projection")
    if stored:
        projection = TimelineEventRead.model_validate(stored)
        if projection.case_id is None:
            projection.case_id = as_str(record.case_id)
        return projection
    source_event = RecognitionEventEnvelope.model_validate(record.payload.get("source_event", {}))
    projection = build_timeline_projection(source_event, source_component=record.source_component)
    projection.case_id = as_str(record.case_id)
    return projection


def read_manual_review_record(record: TimelineEvent) -> ManualReviewRead | None:
    stored = record.payload.get("manual_review_projection")
    if stored:
        if "source_event_type" not in stored:
            stored = {
                **stored,
                "source_event_type": record.event_type,
                "decision": stored.get("decision"),
                "decision_reason": stored.get("decision_reason"),
                "resolved_by": stored.get("resolved_by"),
                "resolved_at": stored.get("resolved_at"),
                "resolution_payload": stored.get("resolution_payload", {}),
                "resolution_event_id": stored.get("resolution_event_id"),
            }
        return ManualReviewRead.model_validate(stored)
    source_event = record.payload.get("source_event")
    if not source_event:
        return None
    return build_manual_review_projection(RecognitionEventEnvelope.model_validate(source_event))


def read_case_suggestion_record(record: TimelineEvent) -> CaseSuggestionRead | None:
    stored = record.payload.get("case_suggestion_projection")
    if stored:
        if "source_event_type" not in stored:
            stored = {
                **stored,
                "source_event_type": record.event_type,
                "decision": stored.get("decision"),
                "decision_reason": stored.get("decision_reason"),
                "resolved_by": stored.get("resolved_by"),
                "resolved_at": stored.get("resolved_at"),
                "resolution_payload": stored.get("resolution_payload", {}),
                "resolution_event_id": stored.get("resolution_event_id"),
                "promoted_case_id": stored.get("promoted_case_id"),
                "promoted_at": stored.get("promoted_at"),
            }
        return CaseSuggestionRead.model_validate(stored)
    source_event = record.payload.get("source_event")
    if not source_event:
        return None
    return build_case_suggestion_projection(RecognitionEventEnvelope.model_validate(source_event))


def list_timeline(
    session: Session,
    *,
    limit: int,
    event_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    organization_id: str | None = None,
    site_id: str | None = None,
    case_id: str | None = None,
) -> list[TimelineEventRead]:
    settings = get_settings()
    safe_limit = max(1, min(limit, settings.max_query_limit))
    stmt = select(TimelineEvent).order_by(TimelineEvent.occurred_at.desc())
    if event_type:
        stmt = stmt.where(TimelineEvent.event_type == event_type)
    parsed_case_id = parse_uuid(case_id)
    if parsed_case_id is not None:
        stmt = stmt.where(TimelineEvent.case_id == parsed_case_id)
    candidate_limit = max(settings.max_query_limit, safe_limit * 5)
    rows = list(session.scalars(stmt.limit(candidate_limit)).all())
    items = [read_timeline_record(row) for row in rows]
    filtered = _filter_timeline_items(
        items,
        camera_id=camera_id,
        subject_id=subject_id,
        organization_id=organization_id,
        site_id=site_id,
    )
    return apply_live_first_order(filtered)[:safe_limit]


def get_timeline_by_source_event_id(session: Session, source_event_id: str) -> TimelineEventRead | None:
    settings = get_settings()
    for component in (settings.default_source_component, settings.workflow_source_component):
        storage_id = build_storage_event_uuid(component, source_event_id)
        row = session.scalar(
            select(TimelineEvent).where(
                TimelineEvent.source_component == component,
                TimelineEvent.source_event_id == storage_id,
            )
        )
        if row is not None:
            return read_timeline_record(row)

    stmt = select(TimelineEvent).order_by(TimelineEvent.occurred_at.desc())
    for candidate in session.scalars(stmt).all():
        item = read_timeline_record(candidate)
        if item.source_event_id == source_event_id:
            return item
    return None


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
    from app.services.manual_review_service import list_manual_reviews as _list_manual_reviews

    return _list_manual_reviews(
        session,
        limit=limit,
        offset=offset,
        status=status,
        review_type=review_type,
        priority=priority,
        camera_id=camera_id,
        subject_id=subject_id,
    )


def get_manual_review(session: Session, review_id: str) -> ManualReviewRead | None:
    from app.services.manual_review_service import get_manual_review as _get_manual_review

    return _get_manual_review(session, review_id)


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
    from app.services.case_suggestion_service import list_case_suggestions as _list_case_suggestions

    return _list_case_suggestions(
        session,
        limit=limit,
        offset=offset,
        status=status,
        suggestion_type=suggestion_type,
        camera_id=camera_id,
        subject_id=subject_id,
    )


def get_case_suggestion(session: Session, suggestion_id: str) -> CaseSuggestionRead | None:
    from app.services.case_suggestion_service import get_case_suggestion as _get_case_suggestion

    return _get_case_suggestion(session, suggestion_id)


def _filter_timeline_items(
    items: list[TimelineEventRead],
    *,
    camera_id: str | None,
    subject_id: str | None,
    organization_id: str | None,
    site_id: str | None,
) -> list[TimelineEventRead]:
    filtered = []
    for item in items:
        if camera_id and item.camera_id != camera_id:
            continue
        if subject_id and item.subject_id != subject_id:
            continue
        if organization_id and item.organization_id != organization_id:
            continue
        if site_id and item.site_id != site_id:
            continue
        filtered.append(item)
    return filtered


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
