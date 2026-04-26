from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CaseRecord, CaseStatusHistory, TimelineEvent
from app.services.case_record_service import CaseRecordRead, case_lifecycle_status, get_case_record_model, read_case_record
from app.services.events import as_str, build_projection_uuid, build_storage_event_uuid, parse_uuid
from app.services.timeline_service import (
    build_action_source_event_id,
    create_audit_timeline_event,
    normalized_workflow_payload,
)
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowValidationError


DB_STATUS_BY_PUBLIC_STATUS = {
    "open": "open",
    "in_review": "under_review",
    "under_review": "under_review",
    "pending_identification": "pending_identification",
    "resolved": "resolved",
    "closed": "resolved",
    "reopened": "open",
    "dismissed": "dismissed",
    "merged": "merged",
}
CASE_CLOSED_EVENT_TYPE = "case_closed"
CASE_REOPENED_EVENT_TYPE = "case_reopened"
CASE_STATUS_CHANGED_EVENT_TYPE = "case_status_changed"


class CaseStatusChangeRequest(BaseModel):
    status: Literal[
        "open",
        "in_review",
        "under_review",
        "pending_identification",
        "resolved",
        "closed",
        "reopened",
        "dismissed",
        "merged",
    ]
    reason: str = Field(min_length=1)
    changed_by: str | None = None
    changed_by_user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseCloseRequest(BaseModel):
    reason: str = Field(min_length=1)
    changed_by: str | None = None
    changed_by_user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseReopenRequest(BaseModel):
    reason: str = Field(min_length=1)
    changed_by: str | None = None
    changed_by_user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def change_case_status(
    session: Session,
    case_id: str,
    request: CaseStatusChangeRequest,
) -> CaseRecordRead:
    public_status, db_status = normalize_case_status(request.status)
    if public_status == "closed":
        return close_case(
            session,
            case_id,
            CaseCloseRequest(
                reason=request.reason,
                changed_by=request.changed_by,
                changed_by_user_id=request.changed_by_user_id,
                metadata=request.metadata,
            ),
        )
    if public_status == "reopened":
        return reopen_case(
            session,
            case_id,
            CaseReopenRequest(
                reason=request.reason,
                changed_by=request.changed_by,
                changed_by_user_id=request.changed_by_user_id,
                metadata=request.metadata,
            ),
        )
    return _apply_case_lifecycle_action(
        session,
        case_id=case_id,
        action_type="status_changed",
        event_type=CASE_STATUS_CHANGED_EVENT_TYPE,
        target_public_status=public_status,
        target_db_status=db_status,
        reason=request.reason,
        changed_by=request.changed_by,
        changed_by_user_id=request.changed_by_user_id,
        action_metadata=request.metadata,
    )


def close_case(session: Session, case_id: str, request: CaseCloseRequest) -> CaseRecordRead:
    return _apply_case_lifecycle_action(
        session,
        case_id=case_id,
        action_type="closed",
        event_type=CASE_CLOSED_EVENT_TYPE,
        target_public_status="closed",
        target_db_status="resolved",
        reason=request.reason,
        changed_by=request.changed_by,
        changed_by_user_id=request.changed_by_user_id,
        action_metadata=request.metadata,
    )


def reopen_case(session: Session, case_id: str, request: CaseReopenRequest) -> CaseRecordRead:
    return _apply_case_lifecycle_action(
        session,
        case_id=case_id,
        action_type="reopened",
        event_type=CASE_REOPENED_EVENT_TYPE,
        target_public_status="reopened",
        target_db_status="open",
        reason=request.reason,
        changed_by=request.changed_by,
        changed_by_user_id=request.changed_by_user_id,
        action_metadata=request.metadata,
    )


def normalize_case_status(status: str) -> tuple[str, str]:
    normalized = status.strip().lower()
    db_status = DB_STATUS_BY_PUBLIC_STATUS.get(normalized)
    if db_status is None:
        raise WorkflowValidationError(f"Unsupported case status: {status}")
    public_status = "in_review" if normalized == "under_review" else normalized
    return public_status, db_status


def _apply_case_lifecycle_action(
    session: Session,
    *,
    case_id: str,
    action_type: str,
    event_type: str,
    target_public_status: str,
    target_db_status: str,
    reason: str,
    changed_by: str | None,
    changed_by_user_id: str | None,
    action_metadata: dict[str, Any],
) -> CaseRecordRead:
    record = get_case_record_model(session, case_id)
    normalized_reason = reason.strip()
    normalized_actor = changed_by.strip()
    if not normalized_reason:
        raise WorkflowValidationError("reason is required")
    if not normalized_actor:
        raise WorkflowValidationError("changed_by is required")

    action_key = _build_lifecycle_action_key(
        case_id=str(record.case_id),
        action_type=action_type,
        target_public_status=target_public_status,
        reason=normalized_reason,
        changed_by=normalized_actor,
        changed_by_user_id=changed_by_user_id,
        action_metadata=action_metadata,
    )
    if _action_already_recorded(session, event_type=event_type, action_key=action_key):
        return read_case_record(record)

    current_public_status = case_lifecycle_status(record)
    if current_public_status == target_public_status and record.case_status == target_db_status:
        raise WorkflowConflictError(f"Case already has status {target_public_status}")
    if action_type == "reopened" and current_public_status not in {"closed", "resolved"} and record.closed_at is None:
        raise WorkflowConflictError("Only closed or resolved cases can be reopened")

    changed_at = datetime.now(timezone.utc)
    old_db_status = record.case_status
    old_public_status = current_public_status
    status_history_id = build_projection_uuid("case-status-history", action_key)

    if session.get(CaseStatusHistory, status_history_id) is None:
        session.add(
            CaseStatusHistory(
                case_status_history_id=status_history_id,
                case_id=record.case_id,
                old_status=old_db_status,
                new_status=target_db_status,
                changed_by_user_id=parse_uuid(changed_by_user_id),
                reason=normalized_reason,
                changed_at=changed_at,
            )
        )

    metadata = dict(record.case_metadata or {})
    metadata["lifecycle_status"] = target_public_status
    metadata["last_lifecycle_action"] = {
        "action_type": action_type,
        "old_status": old_public_status,
        "old_db_status": old_db_status,
        "new_status": target_public_status,
        "new_db_status": target_db_status,
        "reason": normalized_reason,
        "changed_by": normalized_actor,
        "changed_by_user_id": changed_by_user_id,
        "changed_at": changed_at.isoformat(),
        "metadata": dict(action_metadata),
    }
    if action_type == "closed":
        metadata["closed_reason"] = normalized_reason
        metadata["closed_by"] = normalized_actor
    if action_type == "reopened":
        metadata["reopened_reason"] = normalized_reason
        metadata["reopened_by"] = normalized_actor

    record.case_status = target_db_status
    record.case_metadata = metadata
    record.closed_at = changed_at if action_type == "closed" else (None if action_type == "reopened" else record.closed_at)
    record.updated_at = changed_at

    action_payload = {
        "action_type": action_type,
        "case_id": str(record.case_id),
        "case_code": record.case_code,
        "old_status": old_public_status,
        "old_db_status": old_db_status,
        "new_status": target_public_status,
        "new_db_status": target_db_status,
        "reason": normalized_reason,
        "changed_by": normalized_actor,
        "changed_by_user_id": changed_by_user_id,
        "changed_at": changed_at.isoformat(),
        "status_history_id": str(status_history_id),
        "metadata": dict(action_metadata),
    }
    _, created = create_audit_timeline_event(
        session,
        event_type=event_type,
        action_key=action_key,
        summary=_case_lifecycle_summary(record, action_type, target_public_status),
        severity=record.severity,
        payload={"case_lifecycle_action": action_payload},
        occurred_at=changed_at,
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


def _build_lifecycle_action_key(
    *,
    case_id: str,
    action_type: str,
    target_public_status: str,
    reason: str,
    changed_by: str,
    changed_by_user_id: str | None,
    action_metadata: dict[str, Any],
) -> str:
    return normalized_workflow_payload(
        {
            "case_id": case_id,
            "action_type": action_type,
            "target_status": target_public_status,
            "reason": reason,
            "changed_by": changed_by,
            "changed_by_user_id": changed_by_user_id,
            "metadata": action_metadata,
        }
    )


def _action_already_recorded(session: Session, *, event_type: str, action_key: str) -> bool:
    settings = get_settings()
    source_event_id = build_action_source_event_id(event_type, action_key)
    storage_source_event_id = build_storage_event_uuid(settings.workflow_source_component, source_event_id)
    existing = session.scalar(
        select(TimelineEvent).where(
            TimelineEvent.source_component == settings.workflow_source_component,
            TimelineEvent.source_event_id == storage_source_event_id,
        )
    )
    return existing is not None


def _case_lifecycle_summary(record: CaseRecord, action_type: str, target_public_status: str) -> str:
    if action_type == "closed":
        return f"Caso {record.case_code} cerrado"
    if action_type == "reopened":
        return f"Caso {record.case_code} reabierto"
    return f"Caso {record.case_code} cambiado a {target_public_status}"
