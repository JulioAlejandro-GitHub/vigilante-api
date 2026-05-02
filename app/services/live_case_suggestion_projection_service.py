from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.evidence_ref_classifier import evidence_ref_profile, extract_evidence_refs
from app.services.events import (
    CASE_SUGGESTION_EVENT_TYPES,
    RecognitionEventEnvelope,
    TimelineEventRead,
    as_str,
    build_projection_uuid,
    ingest_event,
    read_case_suggestion_record,
    read_timeline_record,
    severity_to_priority,
)
from app.services.timeline_service import list_timeline_rows


CANDIDATE_EVENT_TYPES = {
    "human_presence_no_face",
    "face_detected_unidentified",
    "manual_review_required",
    "identity_conflict",
    "recurrent_unresolved_subject",
}
IMMEDIATE_TRIGGER_EVENT_TYPES = {
    "manual_review_required",
    "identity_conflict",
    "recurrent_unresolved_subject",
}
SEVERITY_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


@dataclass
class CandidateGroup:
    group_type: str
    group_key: str
    items: list[TimelineEventRead] = field(default_factory=list)

    @property
    def latest(self) -> TimelineEventRead:
        return max(self.items, key=lambda item: item.event_ts)

    @property
    def is_immediate(self) -> bool:
        return any(_is_immediate_trigger(item) for item in self.items)


@dataclass(frozen=True)
class ExistingSuggestionIndex:
    source_event_ids: set[str]
    subjects: set[str]
    tracks: set[str]
    cameras: set[str]
    evidence_refs: set[str]


def project_live_case_suggestions_from_timeline(session: Session) -> int:
    """Materialize minimal live case suggestions from recent timeline evidence.

    The projection is intentionally small: it only looks at recent timeline rows
    that already carry live evidence refs and writes deterministic
    ``case_suggestion_created`` projections back into timeline.
    """

    settings = get_settings()
    if not settings.live_case_suggestion_projection_enabled:
        return 0

    candidate_rows = list_timeline_rows(
        session,
        descending=True,
        limit=max(settings.live_projection_max_events, settings.max_query_limit),
    )
    candidates = [_read_candidate(row) for row in candidate_rows]
    candidates = [item for item in candidates if item is not None]
    if not candidates:
        return 0

    existing = _existing_suggestions(session)
    projected = 0
    used_source_event_ids: set[str] = set()

    for group in _qualifying_primary_groups(candidates):
        event = _build_derived_case_suggestion_event(group)
        if _already_covered(event, existing):
            continue
        result = ingest_event(session, event)
        if result.status == "applied":
            projected += 1
            used_source_event_ids.update(_source_event_ids(event.payload))

    remaining_for_camera = [
        item for item in candidates if item.source_event_id not in used_source_event_ids and item.camera_id
    ]
    for group in _qualifying_camera_groups(remaining_for_camera):
        event = _build_derived_case_suggestion_event(group)
        if _already_covered(event, existing):
            continue
        result = ingest_event(session, event)
        if result.status == "applied":
            projected += 1

    return projected


def _read_candidate(row: Any) -> TimelineEventRead | None:
    item = read_timeline_record(row)
    settings = get_settings()
    if item.source_component == settings.workflow_source_component:
        return None
    if item.event_type in CASE_SUGGESTION_EVENT_TYPES:
        return None
    if item.event_type not in CANDIDATE_EVENT_TYPES and not _is_immediate_trigger(item):
        return None
    if item.event_type == "face_detected_identified" and not _is_immediate_trigger(item):
        return None
    if not evidence_ref_profile(item, max_refs=settings.media_resolution_max_refs).has_live:
        return None
    return item


def _qualifying_primary_groups(items: list[TimelineEventRead]) -> list[CandidateGroup]:
    groups: dict[tuple[str, str], CandidateGroup] = {}
    for item in items:
        group_type, group_key = _primary_group_key(item)
        if not group_key:
            continue
        key = (group_type, group_key)
        groups.setdefault(key, CandidateGroup(group_type=group_type, group_key=group_key)).items.append(item)
    return _qualifying_groups(groups.values())


def _qualifying_camera_groups(items: list[TimelineEventRead]) -> list[CandidateGroup]:
    groups: dict[tuple[str, str], CandidateGroup] = {}
    for item in items:
        if not item.camera_id:
            continue
        key = ("camera", item.camera_id)
        groups.setdefault(key, CandidateGroup(group_type="camera", group_key=item.camera_id)).items.append(item)
    return _qualifying_groups(groups.values())


def _qualifying_groups(groups: Iterable[CandidateGroup]) -> list[CandidateGroup]:
    settings = get_settings()
    minimum = max(1, settings.live_case_suggestion_min_events)
    window_minutes = settings.live_case_suggestion_window_minutes
    recent_groups = [
        CandidateGroup(
            group_type=group.group_type,
            group_key=group.group_key,
            items=_best_recent_cluster(group.items, minutes=window_minutes),
        )
        for group in groups
    ]
    qualified = [
        group
        for group in recent_groups
        if group.is_immediate or len(_source_event_ids_from_items(group.items)) >= minimum
    ]
    qualified.sort(key=lambda group: group.latest.event_ts, reverse=True)
    return qualified


def _best_recent_cluster(items: list[TimelineEventRead], *, minutes: int) -> list[TimelineEventRead]:
    sorted_items = sorted(items, key=lambda item: item.event_ts, reverse=True)
    if not sorted_items:
        return []

    window = timedelta(minutes=max(1, minutes))
    immediate_items = [item for item in sorted_items if _is_immediate_trigger(item)]
    anchors = immediate_items or sorted_items
    best: list[TimelineEventRead] = []
    for anchor in anchors:
        window_start = anchor.event_ts - window
        cluster = [item for item in sorted_items if window_start <= item.event_ts <= anchor.event_ts]
        if len(cluster) > len(best):
            best = cluster
    return best


def _primary_group_key(item: TimelineEventRead) -> tuple[str, str | None]:
    if item.subject_id:
        return "subject", item.subject_id
    if item.track_id:
        return "track", item.track_id
    return "camera", item.camera_id


def _is_immediate_trigger(item: TimelineEventRead) -> bool:
    if item.event_type in IMMEDIATE_TRIGGER_EVENT_TYPES:
        return True
    payload = item.payload or {}
    return payload.get("requires_human_review") is True or payload.get("requires_case_evaluation") is True


def _build_derived_case_suggestion_event(group: CandidateGroup) -> RecognitionEventEnvelope:
    latest = group.latest
    sorted_items = sorted(group.items, key=lambda item: item.event_ts, reverse=True)
    source_event_ids = _source_event_ids_from_items(sorted_items)
    evidence_refs = _evidence_refs_from_items(sorted_items)
    severity = _highest_severity(sorted_items)
    suggestion_type = _suggestion_type(group, sorted_items)
    first_ref = evidence_refs[0] if evidence_refs else None
    event_uuid = build_projection_uuid(
        "live-case-suggestion-event",
        group.group_type,
        group.group_key,
        suggestion_type,
        first_ref,
    )
    event_id = f"evt_api_live_case_suggestion_{str(event_uuid).replace('-', '')[:16]}"
    trigger = _trigger_reason(group, sorted_items)
    payload = {
        "severity": severity,
        "confidence": _max_confidence(sorted_items),
        "decision_reason": ["live_case_suggestion_projection", trigger],
        "evidence_refs": evidence_refs,
        "requires_case_evaluation": True,
        "suggestion_type": suggestion_type,
        "suggested_case_type": _suggested_case_type(suggestion_type),
        "suggested_title": _suggested_title(suggestion_type),
        "suggested_reason": _suggested_reason(group, sorted_items),
        "suggested_priority": severity_to_priority(severity),
        "suggested_severity": severity,
        "evidence_count": max(len(evidence_refs), len(source_event_ids)),
        "source_event_ids": source_event_ids,
        "source_event_types": sorted({item.event_type for item in sorted_items}),
        "trigger_source_event_id": latest.source_event_id,
        "trigger_source_event_type": latest.event_type,
        "projection_group_type": group.group_type,
        "projection_group_key": group.group_key,
        "projection_window_minutes": get_settings().live_case_suggestion_window_minutes,
        "current_subject_id": latest.subject_id,
        "current_track_id": latest.track_id,
        "generation_trace": {
            "pipeline": "vigilante-api.live_case_suggestion_projection",
            "step": "minimal_live_projection",
        },
    }
    semantic_descriptor = latest.payload.get("semantic_descriptor") if isinstance(latest.payload, dict) else None
    if isinstance(semantic_descriptor, dict):
        payload["semantic_descriptor"] = semantic_descriptor

    return RecognitionEventEnvelope(
        event_id=event_id,
        event_type="case_suggestion_created",
        event_version="1.0",
        occurred_at=latest.event_ts,
        emitted_at=datetime.now(latest.event_ts.tzinfo),
        source={
            "component": get_settings().workflow_source_component,
            "instance": "live-case-suggestion-projection",
            "version": None,
        },
        payload=payload,
        context={
            "camera_id": latest.camera_id,
            "track_id": latest.track_id,
            "subject_id": latest.subject_id,
            "organization_id": latest.organization_id,
            "site_id": latest.site_id,
            "idempotency_key": f"api-live-case-suggestion:{event_id}",
        },
    )


def _existing_suggestions(session: Session) -> ExistingSuggestionIndex:
    rows = list_timeline_rows(session, event_types=CASE_SUGGESTION_EVENT_TYPES, descending=True)
    source_event_ids: set[str] = set()
    subjects: set[str] = set()
    tracks: set[str] = set()
    cameras: set[str] = set()
    evidence_refs: set[str] = set()
    settings = get_settings()
    for row in rows:
        suggestion = read_case_suggestion_record(row)
        if suggestion is None:
            continue
        if not evidence_ref_profile(suggestion, max_refs=settings.media_resolution_max_refs).has_live:
            continue
        source_event_ids.update(_source_event_ids(suggestion.payload))
        _add(subjects, suggestion.subject_id)
        _add(tracks, suggestion.track_id)
        _add(cameras, suggestion.camera_id)
        evidence_refs.update(extract_evidence_refs(suggestion, max_refs=settings.media_resolution_max_refs))
    return ExistingSuggestionIndex(
        source_event_ids=source_event_ids,
        subjects=subjects,
        tracks=tracks,
        cameras=cameras,
        evidence_refs=evidence_refs,
    )


def _already_covered(event: RecognitionEventEnvelope, existing: ExistingSuggestionIndex) -> bool:
    refs = set(extract_evidence_refs(event.payload, max_refs=get_settings().media_resolution_max_refs))
    if refs & existing.evidence_refs:
        return True
    if _source_event_ids(event.payload) & existing.source_event_ids:
        return True
    context = event.context or {}
    subject_id = as_str(context.get("subject_id"))
    track_id = as_str(context.get("track_id"))
    camera_id = as_str(context.get("camera_id"))
    if subject_id and subject_id in existing.subjects:
        return True
    if track_id and track_id in existing.tracks:
        return True
    return bool(camera_id and camera_id in existing.cameras)


def _suggestion_type(group: CandidateGroup, items: list[TimelineEventRead]) -> str:
    event_types = {item.event_type for item in items}
    if "identity_conflict" in event_types:
        return "identity_conflict"
    if group.is_immediate and "manual_review_required" in event_types:
        return "manual_review"
    return "unresolved_subject_case"


def _suggested_case_type(suggestion_type: str) -> str:
    if suggestion_type == "identity_conflict":
        return "candidate_match_conflict"
    if suggestion_type == "manual_review":
        return "manual_investigation"
    return "multi_event_tracking"


def _suggested_title(suggestion_type: str) -> str:
    if suggestion_type == "identity_conflict":
        return "Conflicto de identidad detectado"
    if suggestion_type == "manual_review":
        return "Evidencia elevada a investigacion"
    return "Sujeto no resuelto recurrente"


def _suggested_reason(group: CandidateGroup, items: list[TimelineEventRead]) -> str:
    if group.is_immediate:
        latest = group.latest
        return f"Evento live {latest.event_type} requiere evaluacion de caso"
    return f"{len(_source_event_ids_from_items(items))} eventos live recientes agrupados por {group.group_type}"


def _trigger_reason(group: CandidateGroup, items: list[TimelineEventRead]) -> str:
    if group.is_immediate:
        return f"{group.latest.event_type}_requires_case_evaluation"
    return f"recent_{group.group_type}_evidence_threshold_passed"


def _highest_severity(items: list[TimelineEventRead]) -> str:
    return max((str(item.severity or "low").lower() for item in items), key=lambda value: SEVERITY_RANK.get(value, 1))


def _max_confidence(items: list[TimelineEventRead]) -> float | None:
    values = [item.confidence for item in items if item.confidence is not None]
    return max(values) if values else None


def _evidence_refs_from_items(items: list[TimelineEventRead]) -> list[str]:
    refs: list[str] = []
    max_refs = get_settings().media_resolution_max_refs
    for item in items:
        refs.extend(extract_evidence_refs(item, max_refs=max_refs))
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
        if len(deduped) >= max_refs:
            break
    return deduped


def _source_event_ids_from_items(items: list[TimelineEventRead]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.source_event_id in seen:
            continue
        seen.add(item.source_event_id)
        ids.append(item.source_event_id)
    return ids


def _source_event_ids(payload: dict[str, Any]) -> set[str]:
    value = payload.get("source_event_ids")
    if isinstance(value, list):
        return {str(item) for item in value if item not in (None, "")}
    value = payload.get("trigger_source_event_id") or payload.get("source_event_id")
    return {str(value)} if value not in (None, "") else set()


def _add(target: set[str], value: Any) -> None:
    string_value = as_str(value)
    if string_value:
        target.add(string_value)
