from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CaseItem, CaseRecord, TimelineEvent
from app.services.case_record_service import get_case_record_model
from app.services.case_suggestion_service import list_case_suggestions
from app.services.events import CaseSuggestionRead, ManualReviewRead, TimelineEventRead, as_str, parse_uuid, read_timeline_record
from app.services.manual_review_service import list_manual_reviews


SUBJECT_KEYS = {"subject_id", "observed_subject_id", "current_subject_id", "target_subject_id", "primary_subject_id"}
TRACK_KEYS = {"track_id", "current_track_id", "latest_track_id", "primary_track_id"}
CAMERA_KEYS = {"camera_id", "last_camera_id", "primary_camera_id"}
PERSON_KEYS = {"person_profile_id", "confirmed_person_profile_id", "discarded_person_profile_id"}
SOURCE_EVENT_KEYS = {"source_event_id", "event_id", "resolution_event_id"}
EVIDENCE_KEYS = {"evidence_ref", "evidence_refs"}


@dataclass
class CaseRelationRefs:
    subject_ids: set[str] = field(default_factory=set)
    track_ids: set[str] = field(default_factory=set)
    camera_ids: set[str] = field(default_factory=set)
    person_profile_ids: set[str] = field(default_factory=set)
    source_event_ids: set[str] = field(default_factory=set)
    suggestion_ids: set[str] = field(default_factory=set)
    evidence_refs: set[str] = field(default_factory=set)

    @property
    def entity_refs(self) -> set[str]:
        return self.subject_ids | self.track_ids | self.camera_ids | self.person_profile_ids


def list_case_related_reviews(session: Session, case_id: str, *, limit: int) -> list[ManualReviewRead]:
    record = get_case_record_model(session, case_id)
    refs = collect_case_relation_refs(session, record)
    reviews = list_manual_reviews(session, limit=get_settings().max_query_limit)
    related = [review for review in reviews if _manual_review_related_to_case(review, refs)]
    return related[:_safe_limit(limit)]


def list_case_related_suggestions(session: Session, case_id: str, *, limit: int) -> list[CaseSuggestionRead]:
    record = get_case_record_model(session, case_id)
    refs = collect_case_relation_refs(session, record)
    suggestions = list_case_suggestions(session, limit=get_settings().max_query_limit)
    related = [suggestion for suggestion in suggestions if _case_suggestion_related_to_case(suggestion, str(record.case_id), refs)]
    return related[:_safe_limit(limit)]


def list_case_timeline(session: Session, case_id: str, *, limit: int) -> list[TimelineEventRead]:
    record = get_case_record_model(session, case_id)
    refs = collect_case_relation_refs(session, record)
    related_reviews = list_case_related_reviews(session, str(record.case_id), limit=get_settings().max_query_limit)
    related_suggestions = list_case_related_suggestions(session, str(record.case_id), limit=get_settings().max_query_limit)
    review_ids = {item.review_id for item in related_reviews}
    suggestion_ids = {item.suggestion_id for item in related_suggestions}
    source_event_ids = refs.source_event_ids | {item.source_event_id for item in related_reviews} | {
        item.source_event_id for item in related_suggestions
    }

    rows = session.scalars(select(TimelineEvent).order_by(TimelineEvent.occurred_at.asc())).all()
    items: list[TimelineEventRead] = []
    seen: set[str] = set()
    for row in rows:
        projection = read_timeline_record(row)
        if not _timeline_row_related_to_case(
            row,
            projection,
            record=record,
            refs=refs,
            source_event_ids=source_event_ids,
            review_ids=review_ids,
            suggestion_ids=suggestion_ids,
        ):
            continue
        if projection.case_id is None:
            projection.case_id = str(record.case_id)
        dedupe_key = f"{projection.source_component}:{projection.source_event_id}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(projection)
    return items[:_safe_limit(limit)]


def collect_case_relation_refs(session: Session, record: CaseRecord) -> CaseRelationRefs:
    refs = CaseRelationRefs()
    metadata = dict(record.case_metadata or {})
    _add_ref(refs.subject_ids, record.primary_observed_subject_id)
    _add_ref(refs.camera_ids, record.primary_camera_id)
    _add_ref(refs.person_profile_ids, record.primary_person_profile_id)
    _add_ref(refs.source_event_ids, metadata.get("source_event_id"))
    _add_ref(refs.suggestion_ids, metadata.get("source_suggestion_id"))
    _add_ref(refs.subject_ids, metadata.get("raw_primary_subject_id"))
    _add_ref(refs.camera_ids, metadata.get("raw_primary_camera_id"))
    _collect_payload_refs(metadata.get("source_case_suggestion_payload"), refs)
    _collect_case_item_refs(session, record, refs)
    return refs


def _collect_case_item_refs(session: Session, record: CaseRecord, refs: CaseRelationRefs) -> None:
    rows = session.scalars(select(CaseItem).where(CaseItem.case_id == record.case_id)).all()
    for row in rows:
        item_type = row.item_type
        if item_type == "recognition_event":
            _add_ref(refs.source_event_ids, row.item_ref_text)
        elif item_type == "observed_subject":
            _add_ref(refs.subject_ids, row.item_ref_uuid)
        elif item_type == "person_profile":
            _add_ref(refs.person_profile_ids, row.item_ref_uuid)
        elif item_type == "external_reference" and row.item_ref_text and row.item_ref_text.startswith("source_suggestion_id:"):
            _add_ref(refs.suggestion_ids, row.item_ref_text.split(":", 1)[1])


def _manual_review_related_to_case(review: ManualReviewRead, refs: CaseRelationRefs) -> bool:
    if review.source_event_id in refs.source_event_ids:
        return True
    if _intersects(refs.subject_ids, [review.subject_id]):
        return True
    if _intersects(refs.track_ids, [review.track_id]):
        return True
    if _intersects(refs.camera_ids, [review.camera_id]):
        return True
    review_refs = CaseRelationRefs()
    _collect_payload_refs(review.payload, review_refs)
    return bool(refs.entity_refs & review_refs.entity_refs or refs.evidence_refs & review_refs.evidence_refs)


def _case_suggestion_related_to_case(suggestion: CaseSuggestionRead, case_id: str, refs: CaseRelationRefs) -> bool:
    if suggestion.promoted_case_id == case_id:
        return True
    if suggestion.suggestion_id in refs.suggestion_ids:
        return True
    if suggestion.source_event_id in refs.source_event_ids:
        return True
    if _intersects(refs.subject_ids, [suggestion.subject_id]):
        return True
    if _intersects(refs.track_ids, [suggestion.track_id]):
        return True
    if _intersects(refs.camera_ids, [suggestion.camera_id]):
        return True
    suggestion_refs = CaseRelationRefs()
    _collect_payload_refs(suggestion.payload, suggestion_refs)
    return bool(refs.entity_refs & suggestion_refs.entity_refs or refs.evidence_refs & suggestion_refs.evidence_refs)


def _timeline_row_related_to_case(
    row: TimelineEvent,
    projection: TimelineEventRead,
    *,
    record: CaseRecord,
    refs: CaseRelationRefs,
    source_event_ids: set[str],
    review_ids: set[str],
    suggestion_ids: set[str],
) -> bool:
    if row.case_id == record.case_id or projection.case_id == str(record.case_id):
        return True
    if projection.source_event_id in source_event_ids:
        return True
    payload = row.payload or {}
    if _payload_case_id(payload) == str(record.case_id):
        return True
    if _payload_review_id(payload) in review_ids:
        return True
    suggestion_id = _payload_suggestion_id(payload)
    if suggestion_id in suggestion_ids or suggestion_id in refs.suggestion_ids:
        return True
    row_refs = CaseRelationRefs()
    _collect_payload_refs(payload.get("source_event"), row_refs)
    _collect_payload_refs(payload.get("timeline_projection"), row_refs)
    return bool(refs.entity_refs & row_refs.entity_refs or refs.evidence_refs & row_refs.evidence_refs)


def _payload_case_id(payload: dict[str, Any]) -> str | None:
    for key in ("case_lifecycle_action", "case_note_added", "case_record_created", "action_event"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        if key == "action_event":
            context = value.get("context")
            if isinstance(context, dict):
                case_id = as_str(context.get("case_id"))
                if case_id:
                    return case_id
        case_id = as_str(value.get("case_id"))
        if case_id:
            return case_id
    return None


def _payload_review_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("manual_review_resolution")
    return as_str(value.get("review_id")) if isinstance(value, dict) else None


def _payload_suggestion_id(payload: dict[str, Any]) -> str | None:
    for key in ("case_suggestion_resolution", "case_record_created"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        suggestion_id = as_str(value.get("suggestion_id") or value.get("source_suggestion_id"))
        if suggestion_id:
            return suggestion_id
    return None


def _collect_payload_refs(value: Any, refs: CaseRelationRefs, current_key: str | None = None) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = key.lower()
            if normalized_key in SUBJECT_KEYS:
                _add_nested_refs(refs.subject_ids, nested)
            elif normalized_key in TRACK_KEYS:
                _add_nested_refs(refs.track_ids, nested)
            elif normalized_key in CAMERA_KEYS:
                _add_nested_refs(refs.camera_ids, nested)
            elif normalized_key in PERSON_KEYS:
                _add_nested_refs(refs.person_profile_ids, nested)
            elif normalized_key in SOURCE_EVENT_KEYS:
                _add_nested_refs(refs.source_event_ids, nested, require_uuid=False)
            elif normalized_key in EVIDENCE_KEYS:
                _add_nested_refs(refs.evidence_refs, nested, require_uuid=False)
            _collect_payload_refs(nested, refs, normalized_key)
        return
    if isinstance(value, list):
        for nested in value:
            _collect_payload_refs(nested, refs, current_key)


def _add_nested_refs(target: set[str], value: Any, *, require_uuid: bool = True) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _add_nested_refs(target, nested, require_uuid=require_uuid)
        return
    if isinstance(value, list):
        for nested in value:
            _add_nested_refs(target, nested, require_uuid=require_uuid)
        return
    if require_uuid and parse_uuid(value) is None:
        return
    _add_ref(target, value)


def _add_ref(target: set[str], value: Any) -> None:
    string_value = as_str(value)
    if string_value:
        target.add(string_value)


def _intersects(target: set[str], values: list[str | None]) -> bool:
    return any(value in target for value in values if value)


def _safe_limit(limit: int) -> int:
    settings = get_settings()
    return max(1, min(limit, settings.max_query_limit))
