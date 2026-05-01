from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CaseRecord
from app.services.case_note_service import CaseNoteRead, list_case_notes
from app.services.case_record_service import CaseRecordRead, case_lifecycle_status, get_case_record_model, read_case_record
from app.services.case_relation_service import (
    list_case_related_reviews,
    list_case_related_suggestions,
    list_case_timeline,
)
from app.services.events import CaseSuggestionRead, ManualReviewRead, TimelineEventRead, as_str
from app.services.live_first_read_service import (
    apply_live_first_order,
    remove_fixture_only_items_when_live,
    timeline_has_live_evidence,
)


CASE_SORT_FIELDS = {"updated_at", "opened_at", "priority"}


class CaseDetailRead(CaseRecordRead):
    notes: list[CaseNoteRead] = Field(default_factory=list)
    reviews: list[ManualReviewRead] = Field(default_factory=list)
    suggestions: list[CaseSuggestionRead] = Field(default_factory=list)
    timeline: list[TimelineEventRead] = Field(default_factory=list)


def list_cases_filtered(
    session: Session,
    *,
    limit: int,
    offset: int = 0,
    status: str | None = None,
    assigned_to: str | None = None,
    priority: int | None = None,
    severity: str | None = None,
    case_type: str | None = None,
    organization_id: str | None = None,
    site_id: str | None = None,
    q: str | None = None,
    sort_by: Literal["updated_at", "opened_at", "priority"] = "updated_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> list[CaseRecordRead]:
    settings = get_settings()
    safe_limit = max(1, min(limit, settings.max_query_limit))
    safe_offset = max(0, offset)
    rows = list(session.scalars(select(CaseRecord)).all())
    filtered = [
        row
        for row in rows
        if _case_matches(
            row,
            status=status,
            assigned_to=assigned_to,
            priority=priority,
            severity=severity,
            case_type=case_type,
            organization_id=organization_id,
            site_id=site_id,
            q=q,
        )
    ]
    normalized_sort = sort_by if sort_by in CASE_SORT_FIELDS else "updated_at"
    reverse = sort_order != "asc"
    filtered.sort(key=lambda row: _case_sort_value(row, normalized_sort), reverse=reverse)
    items = [read_case_record(row) for row in filtered]
    if not settings.include_fixture_projections_when_live:
        items = remove_fixture_only_items_when_live(items, live_evidence_exists=timeline_has_live_evidence(session))
    items = apply_live_first_order(items)
    return items[safe_offset : safe_offset + safe_limit]


def get_case_detail(session: Session, case_id: str, *, recent_limit: int) -> CaseDetailRead:
    settings = get_settings()
    safe_limit = max(1, min(recent_limit, settings.max_query_limit))
    record = get_case_record_model(session, case_id)
    base = read_case_record(record)
    return CaseDetailRead(
        **base.model_dump(),
        notes=list_case_notes(session, case_id)[:safe_limit],
        reviews=list_case_related_reviews(session, case_id, limit=safe_limit),
        suggestions=list_case_related_suggestions(session, case_id, limit=safe_limit),
        timeline=list_case_timeline(session, case_id, limit=safe_limit),
    )


def _case_matches(
    row: CaseRecord,
    *,
    status: str | None,
    assigned_to: str | None,
    priority: int | None,
    severity: str | None,
    case_type: str | None,
    organization_id: str | None,
    site_id: str | None,
    q: str | None,
) -> bool:
    projection = read_case_record(row)
    metadata = dict(row.case_metadata or {})
    if status and not _status_matches(row, status):
        return False
    if assigned_to and projection.assigned_to != assigned_to:
        return False
    if priority is not None and projection.priority != priority:
        return False
    if severity and projection.severity != severity:
        return False
    if case_type and projection.case_type != case_type:
        return False
    if organization_id and organization_id not in {as_str(row.organization_id), as_str(metadata.get("raw_organization_id"))}:
        return False
    if site_id and site_id not in {as_str(row.site_id), as_str(metadata.get("raw_site_id"))}:
        return False
    if q and not _text_matches(row, metadata, q):
        return False
    return True


def _status_matches(row: CaseRecord, status: str) -> bool:
    normalized = status.strip().lower()
    public_status = case_lifecycle_status(row)
    if normalized == public_status:
        return True
    if normalized == row.case_status:
        return True
    if normalized == "under_review" and public_status == "in_review":
        return True
    return False


def _text_matches(row: CaseRecord, metadata: dict, q: str) -> bool:
    needle = q.strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        value
        for value in [
            row.case_code,
            row.title,
            row.description or "",
            as_str(metadata.get("source_event_id")) or "",
            as_str(metadata.get("source_suggestion_id")) or "",
        ]
        if value
    ).lower()
    return needle in haystack


def _case_sort_value(row: CaseRecord, sort_by: str) -> datetime | int:
    if sort_by == "opened_at":
        return row.opened_at
    if sort_by == "priority":
        return int(row.priority)
    return row.updated_at
