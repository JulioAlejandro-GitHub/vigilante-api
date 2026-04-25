from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CaseNote, TimelineEvent
from app.services.case_record_service import get_case_record_model
from app.services.events import as_str, build_projection_uuid, parse_uuid
from app.services.timeline_service import create_audit_timeline_event, normalized_workflow_payload
from app.services.workflow_exceptions import WorkflowNotFoundError, WorkflowValidationError


CASE_NOTE_ADDED_EVENT_TYPE = "case_note_added"


class CaseNoteCreateRequest(BaseModel):
    author: str = Field(min_length=1)
    note_text: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseNoteRead(BaseModel):
    note_id: str
    case_id: str
    author: str
    note_text: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


def list_case_notes(session: Session, case_id: str) -> list[CaseNoteRead]:
    record = get_case_record_model(session, case_id)
    author_payloads = _case_note_audit_payloads(session, str(record.case_id))
    rows = session.scalars(
        select(CaseNote).where(CaseNote.case_id == record.case_id).order_by(CaseNote.created_at.desc())
    ).all()
    return [_read_case_note(row, author_payloads.get(str(row.case_note_id))) for row in rows]


def add_case_note(session: Session, case_id: str, request: CaseNoteCreateRequest) -> CaseNoteRead:
    record = get_case_record_model(session, case_id)
    author = request.author.strip()
    note_text = request.note_text.strip()
    if not author:
        raise WorkflowValidationError("author is required")
    if not note_text:
        raise WorkflowValidationError("note_text is required")

    note_id = build_projection_uuid(
        "case-note",
        str(record.case_id),
        author,
        note_text,
        normalized_workflow_payload(dict(request.metadata)),
    )
    action_key = normalized_workflow_payload(
        {
            "case_id": str(record.case_id),
            "action_type": "note_added",
            "note_id": str(note_id),
            "author": author,
            "note_text": note_text,
            "metadata": dict(request.metadata),
        }
    )
    created_at = datetime.now(timezone.utc)
    note = session.get(CaseNote, note_id)
    note_was_created = False
    if note is None:
        note = CaseNote(
            case_note_id=note_id,
            case_id=record.case_id,
            note_text=note_text,
            created_at=created_at,
        )
        session.add(note)
        note_was_created = True
    else:
        created_at = note.created_at

    action_payload = {
        "action_type": "note_added",
        "note_id": str(note_id),
        "case_id": str(record.case_id),
        "case_code": record.case_code,
        "author": author,
        "note_text": note_text,
        "created_at": created_at.isoformat(),
        "metadata": dict(request.metadata),
    }
    _, timeline_created = create_audit_timeline_event(
        session,
        event_type=CASE_NOTE_ADDED_EVENT_TYPE,
        action_key=action_key,
        summary=f"Nota agregada al caso {record.case_code}",
        severity=record.severity,
        payload={"case_note_added": action_payload},
        occurred_at=created_at,
        camera_id=as_str(record.primary_camera_id),
        subject_id=as_str(record.primary_observed_subject_id),
        organization_id=as_str(record.organization_id),
        site_id=as_str(record.site_id),
        person_profile_id=as_str(record.primary_person_profile_id),
        case_id=str(record.case_id),
    )
    if note_was_created or timeline_created:
        session.commit()
    else:
        session.rollback()

    refreshed = session.get(CaseNote, note_id)
    if refreshed is None:
        raise WorkflowNotFoundError("Case note not found after write")
    return _read_case_note(refreshed, action_payload)


def _read_case_note(row: CaseNote, audit_payload: dict[str, Any] | None) -> CaseNoteRead:
    payload = audit_payload or {}
    return CaseNoteRead(
        note_id=str(row.case_note_id),
        case_id=str(row.case_id),
        author=str(payload.get("author") or "unknown"),
        note_text=row.note_text,
        created_at=row.created_at,
        metadata=dict(payload.get("metadata") or {}),
    )


def _case_note_audit_payloads(session: Session, case_id: str) -> dict[str, dict[str, Any]]:
    parsed_case_id = parse_uuid(case_id)
    if parsed_case_id is None:
        return {}
    rows = session.scalars(
        select(TimelineEvent)
        .where(TimelineEvent.case_id == parsed_case_id)
        .where(TimelineEvent.event_type == CASE_NOTE_ADDED_EVENT_TYPE)
        .order_by(TimelineEvent.occurred_at.desc())
    ).all()
    payloads: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = (row.payload or {}).get("case_note_added")
        if not isinstance(payload, dict):
            continue
        note_id = payload.get("note_id")
        if note_id is not None and str(note_id) not in payloads:
            payloads[str(note_id)] = payload
    return payloads
