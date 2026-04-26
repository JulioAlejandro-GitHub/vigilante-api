from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.services.case_record_service import (
    CaseRecordRead,
    get_case_record_model,
    read_case_assignment,
    read_case_record,
)
from app.services.events import as_str, parse_uuid
from app.services.timeline_service import create_audit_timeline_event, normalized_workflow_payload
from app.services.workflow_exceptions import WorkflowValidationError


CASE_ASSIGNED_EVENT_TYPE = "case_assigned"
CASE_REASSIGNED_EVENT_TYPE = "case_reassigned"
CASE_UNASSIGNED_EVENT_TYPE = "case_unassigned"


class CaseAssignRequest(BaseModel):
    assigned_to: str = Field(min_length=1)
    assigned_by: str | None = None
    assigned_to_user_id: str | None = None
    assigned_by_user_id: str | None = None
    assignment_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseUnassignRequest(BaseModel):
    assigned_by: str | None = None
    assigned_by_user_id: str | None = None
    assignment_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def assign_case(session: Session, case_id: str, request: CaseAssignRequest) -> CaseRecordRead:
    record = get_case_record_model(session, case_id)
    assigned_to = request.assigned_to.strip()
    assigned_by = (request.assigned_by or "").strip()
    assignment_reason = _normalize_optional_text(request.assignment_reason)
    if not assigned_to:
        raise WorkflowValidationError("assigned_to is required")
    if not assigned_by:
        raise WorkflowValidationError("assigned_by is required")

    current_assignment = read_case_assignment(record)
    previous_assigned_to = current_assignment.get("assigned_to")
    if previous_assigned_to == assigned_to:
        return read_case_record(record)

    action_type = "reassigned" if previous_assigned_to else "assigned"
    event_type = CASE_REASSIGNED_EVENT_TYPE if previous_assigned_to else CASE_ASSIGNED_EVENT_TYPE
    assigned_at = datetime.now(timezone.utc)
    action_key = normalized_workflow_payload(
        {
            "case_id": str(record.case_id),
            "action_type": action_type,
            "previous_assigned_to": previous_assigned_to,
            "assigned_to": assigned_to,
            "assigned_by": assigned_by,
            "assignment_reason": assignment_reason,
            "metadata": dict(request.metadata),
        }
    )

    metadata = dict(record.case_metadata or {})
    metadata["assignment"] = {
        "assigned_to": assigned_to,
        "assigned_by": assigned_by,
        "assigned_to_user_id": request.assigned_to_user_id,
        "assigned_by_user_id": request.assigned_by_user_id,
        "assigned_at": assigned_at.isoformat(),
        "assignment_reason": assignment_reason,
        "metadata": dict(request.metadata),
    }
    metadata["last_assignment_action"] = {
        "action_type": action_type,
        "previous_assigned_to": previous_assigned_to,
        "assigned_to": assigned_to,
        "assigned_by": assigned_by,
        "assigned_to_user_id": request.assigned_to_user_id,
        "assigned_by_user_id": request.assigned_by_user_id,
        "assigned_at": assigned_at.isoformat(),
        "assignment_reason": assignment_reason,
        "metadata": dict(request.metadata),
    }
    record.case_metadata = metadata
    record.assigned_to_user_id = parse_uuid(request.assigned_to_user_id)
    record.updated_at = assigned_at

    action_payload = {
        "action_type": action_type,
        "case_id": str(record.case_id),
        "case_code": record.case_code,
        "previous_assigned_to": previous_assigned_to,
        "assigned_to": assigned_to,
        "assigned_by": assigned_by,
        "assigned_to_user_id": request.assigned_to_user_id,
        "assigned_by_user_id": request.assigned_by_user_id,
        "assigned_at": assigned_at.isoformat(),
        "assignment_reason": assignment_reason,
        "metadata": dict(request.metadata),
    }
    _, created = create_audit_timeline_event(
        session,
        event_type=event_type,
        action_key=action_key,
        summary=_assignment_summary(record.case_code, action_type, assigned_to),
        severity=record.severity,
        payload={"case_assignment": action_payload},
        occurred_at=assigned_at,
        camera_id=as_str(record.primary_camera_id),
        subject_id=as_str(record.primary_observed_subject_id),
        organization_id=as_str(record.organization_id),
        site_id=as_str(record.site_id),
        person_profile_id=as_str(record.primary_person_profile_id),
        case_id=str(record.case_id),
    )
    if not created:
        session.rollback()
        return read_case_record(get_case_record_model(session, case_id))

    session.commit()
    return read_case_record(record)


def unassign_case(session: Session, case_id: str, request: CaseUnassignRequest) -> CaseRecordRead:
    record = get_case_record_model(session, case_id)
    assigned_by = (request.assigned_by or "").strip()
    assignment_reason = _normalize_optional_text(request.assignment_reason)
    if not assigned_by:
        raise WorkflowValidationError("assigned_by is required")

    current_assignment = read_case_assignment(record)
    previous_assigned_to = current_assignment.get("assigned_to")
    if previous_assigned_to is None:
        return read_case_record(record)

    unassigned_at = datetime.now(timezone.utc)
    action_key = normalized_workflow_payload(
        {
            "case_id": str(record.case_id),
            "action_type": "unassigned",
            "previous_assigned_to": previous_assigned_to,
            "assigned_by": assigned_by,
            "assignment_reason": assignment_reason,
            "metadata": dict(request.metadata),
        }
    )

    metadata = dict(record.case_metadata or {})
    metadata.pop("assignment", None)
    metadata["last_assignment_action"] = {
        "action_type": "unassigned",
        "previous_assigned_to": previous_assigned_to,
        "assigned_to": None,
        "assigned_by": assigned_by,
        "assigned_to_user_id": None,
        "assigned_by_user_id": request.assigned_by_user_id,
        "assigned_at": unassigned_at.isoformat(),
        "assignment_reason": assignment_reason,
        "metadata": dict(request.metadata),
    }
    record.case_metadata = metadata
    record.assigned_to_user_id = None
    record.updated_at = unassigned_at

    action_payload = {
        "action_type": "unassigned",
        "case_id": str(record.case_id),
        "case_code": record.case_code,
        "previous_assigned_to": previous_assigned_to,
        "assigned_to": None,
        "assigned_by": assigned_by,
        "assigned_by_user_id": request.assigned_by_user_id,
        "assigned_at": unassigned_at.isoformat(),
        "assignment_reason": assignment_reason,
        "metadata": dict(request.metadata),
    }
    _, created = create_audit_timeline_event(
        session,
        event_type=CASE_UNASSIGNED_EVENT_TYPE,
        action_key=action_key,
        summary=f"Caso {record.case_code} desasignado",
        severity=record.severity,
        payload={"case_assignment": action_payload},
        occurred_at=unassigned_at,
        camera_id=as_str(record.primary_camera_id),
        subject_id=as_str(record.primary_observed_subject_id),
        organization_id=as_str(record.organization_id),
        site_id=as_str(record.site_id),
        person_profile_id=as_str(record.primary_person_profile_id),
        case_id=str(record.case_id),
    )
    if not created:
        session.rollback()
        return read_case_record(get_case_record_model(session, case_id))

    session.commit()
    return read_case_record(record)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _assignment_summary(case_code: str, action_type: str, assigned_to: str) -> str:
    if action_type == "reassigned":
        return f"Caso {case_code} reasignado a {assigned_to}"
    return f"Caso {case_code} asignado a {assigned_to}"
