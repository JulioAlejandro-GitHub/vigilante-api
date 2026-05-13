from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.timeline_service import (
    create_audit_timeline_event,
    list_timeline_rows,
    normalized_workflow_payload,
)
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

logger = logging.getLogger(__name__)

RECOMMENDATION_STATUS_PENDING = "pending"
RECOMMENDATION_STATUS_APPROVED = "approved"
RECOMMENDATION_STATUS_REJECTED = "rejected"
RECOMMENDATION_STATUS_APPLIED = "applied"
RECOMMENDATION_STATUS_FAILED = "failed"
RECOMMENDATION_STATUS_ROLLED_BACK = "rolled_back"

RECOMMENDATION_STATUSES = {
    RECOMMENDATION_STATUS_PENDING,
    RECOMMENDATION_STATUS_APPROVED,
    RECOMMENDATION_STATUS_REJECTED,
    RECOMMENDATION_STATUS_APPLIED,
    RECOMMENDATION_STATUS_FAILED,
    RECOMMENDATION_STATUS_ROLLED_BACK,
}

RECOMMENDATION_EVENT_APPROVED = "camera_recommendation_approved"
RECOMMENDATION_EVENT_REJECTED = "camera_recommendation_rejected"
RECOMMENDATION_EVENT_APPLIED = "camera_recommendation_applied"
RECOMMENDATION_EVENT_FAILED = "camera_recommendation_failed"
RECOMMENDATION_EVENT_ROLLED_BACK = "camera_recommendation_rolled_back"
RECOMMENDATION_WORKFLOW_EVENT_TYPES = {
    RECOMMENDATION_EVENT_APPROVED,
    RECOMMENDATION_EVENT_REJECTED,
    RECOMMENDATION_EVENT_APPLIED,
    RECOMMENDATION_EVENT_FAILED,
    RECOMMENDATION_EVENT_ROLLED_BACK,
}

DEFAULT_RECOMMENDATIONS_FILENAME = "recommendations.jsonl"

FACE_TUNING_PATCH_FIELDS = {
    "det_size",
    "detection_threshold",
    "max_faces",
    "face_quality_threshold",
    "min_face_bbox_size",
    "min_face_area_ratio",
}
VLM_POLICY_PATCH_FIELDS = {
    "enabled",
    "force_simple",
    "backend",
    "preferred_backend",
    "secondary_backend",
    "enabled_event_types",
    "disabled_event_types",
    "max_allowed_latency_seconds",
    "max_allowed_rss_mb",
    "qwen_max_allowed_rss_mb",
    "smolvlm_max_allowed_rss_mb",
    "max_concurrent_inferences",
    "degradation_policy",
}
VLM_POLICY_FIELD_ALIASES = {
    "enable_for_event_types": "enabled_event_types",
    "disable_for_event_types": "disabled_event_types",
    "max_latency_seconds": "max_allowed_latency_seconds",
    "max_rss_mb": "max_allowed_rss_mb",
}
APPLICABLE_RECOMMENDATION_TYPES = {"face_tuning", "vlm_policy", "budget", "event_policy"}


class CameraRecommendationDecisionRequest(BaseModel):
    comment: str | None = None


class CameraRecommendationRead(BaseModel):
    recommendation_id: str
    camera_id: str
    status: Literal["pending", "approved", "rejected", "applied", "failed", "rolled_back"]
    recommendation_type: str
    current_value: Any = None
    suggested_value: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None
    actionable: bool = False
    applicable: bool = False
    metadata_paths: list[str] = Field(default_factory=list)
    impact: str | None = None
    severity: str | None = None
    title: str | None = None
    reason: str | None = None
    confidence: float | None = None
    metrics_used: list[str] = Field(default_factory=list)
    window_summary: dict[str, Any] = Field(default_factory=dict)
    rule_set_version: str | None = None
    source_status: str | None = None
    auto_apply: bool = False
    workflow: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None


@dataclass(frozen=True)
class RecommendationWorkflowState:
    status: str = RECOMMENDATION_STATUS_PENDING
    event_type: str | None = None
    event_id: str | None = None
    actor: str | None = None
    actor_user_id: str | None = None
    comment: str | None = None
    occurred_at: str | None = None
    result: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None


def list_recommendations(
    session: Session,
    *,
    status: str | None = None,
    camera_id: str | None = None,
    actionable: bool | None = None,
    limit: int | None = None,
    offset: int = 0,
    source_path: str | Path | None = None,
) -> list[CameraRecommendationRead]:
    settings = get_settings()
    records = read_persisted_recommendations(
        source_path or settings.recognition_recommendations_path,
        max_records=settings.recognition_recommendations_max_records,
    )
    states = workflow_states_by_recommendation(session)
    items = [_to_read(record, states.get(str(record["recommendation_id"]))) for record in records]

    filtered: list[CameraRecommendationRead] = []
    for item in items:
        if status and item.status != status:
            continue
        if camera_id and item.camera_id != camera_id:
            continue
        if actionable is not None and item.actionable is not actionable:
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: item.generated_at or "", reverse=True)
    safe_offset = max(0, int(offset or 0))
    if limit is None or limit <= 0:
        return filtered[safe_offset:]
    return filtered[safe_offset : safe_offset + max(1, int(limit))]


def get_recommendation(
    session: Session,
    recommendation_id: str,
    *,
    source_path: str | Path | None = None,
) -> CameraRecommendationRead:
    for item in list_recommendations(session, source_path=source_path):
        if item.recommendation_id == recommendation_id:
            return item
    raise WorkflowNotFoundError("Recommendation not found")


def approve_recommendation(
    session: Session,
    recommendation_id: str,
    *,
    actor: str,
    actor_user_id: str | None,
    comment: str | None = None,
    source_path: str | Path | None = None,
) -> CameraRecommendationRead:
    recommendation = get_recommendation(session, recommendation_id, source_path=source_path)
    if recommendation.status == RECOMMENDATION_STATUS_APPROVED:
        return recommendation
    if recommendation.status != RECOMMENDATION_STATUS_PENDING:
        raise WorkflowConflictError(f"Recommendation is {recommendation.status} and cannot be approved")

    _require_actor(actor)
    record_recommendation_workflow_event(
        session,
        recommendation=recommendation,
        event_type=RECOMMENDATION_EVENT_APPROVED,
        status_before=recommendation.status,
        status_after=RECOMMENDATION_STATUS_APPROVED,
        actor=actor,
        actor_user_id=actor_user_id,
        comment=comment,
        result={"status": "approved"},
    )
    session.commit()
    return get_recommendation(session, recommendation_id, source_path=source_path)


def reject_recommendation(
    session: Session,
    recommendation_id: str,
    *,
    actor: str,
    actor_user_id: str | None,
    comment: str | None = None,
    source_path: str | Path | None = None,
) -> CameraRecommendationRead:
    recommendation = get_recommendation(session, recommendation_id, source_path=source_path)
    if recommendation.status == RECOMMENDATION_STATUS_REJECTED:
        return recommendation
    if recommendation.status not in {RECOMMENDATION_STATUS_PENDING, RECOMMENDATION_STATUS_APPROVED}:
        raise WorkflowConflictError(f"Recommendation is {recommendation.status} and cannot be rejected")

    _require_actor(actor)
    record_recommendation_workflow_event(
        session,
        recommendation=recommendation,
        event_type=RECOMMENDATION_EVENT_REJECTED,
        status_before=recommendation.status,
        status_after=RECOMMENDATION_STATUS_REJECTED,
        actor=actor,
        actor_user_id=actor_user_id,
        comment=comment,
        result={"status": "rejected"},
    )
    session.commit()
    return get_recommendation(session, recommendation_id, source_path=source_path)


def record_recommendation_workflow_event(
    session: Session,
    *,
    recommendation: CameraRecommendationRead,
    event_type: str,
    status_before: str,
    status_after: str,
    actor: str,
    actor_user_id: str | None,
    comment: str | None = None,
    result: dict[str, Any] | None = None,
    application: dict[str, Any] | None = None,
    rollback: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    if status_after not in RECOMMENDATION_STATUSES:
        raise WorkflowValidationError(f"Invalid recommendation status: {status_after}")
    _require_actor(actor)
    event_ts = occurred_at or datetime.now(timezone.utc)
    payload = {
        "recommendation_id": recommendation.recommendation_id,
        "camera_id": recommendation.camera_id,
        "recommendation_type": recommendation.recommendation_type,
        "status_before": status_before,
        "status_after": status_after,
        "actor": actor,
        "actor_user_id": actor_user_id,
        "comment": comment,
        "occurred_at": event_ts.isoformat(),
        "recommendation_snapshot": recommendation.model_dump(mode="json"),
        "result": result or {},
        "application": application,
        "rollback": rollback,
    }
    action_key = normalized_workflow_payload(
        {
            "recommendation_id": recommendation.recommendation_id,
            "event_type": event_type,
            "status_before": status_before,
            "status_after": status_after,
            "actor_user_id": actor_user_id,
            "occurred_at": event_ts.isoformat(),
            "result": result or {},
        }
    )
    timeline_event, created = create_audit_timeline_event(
        session,
        event_type=event_type,
        action_key=action_key,
        summary=_workflow_summary(event_type, recommendation),
        severity=recommendation.severity or "medium",
        payload={"camera_recommendation_workflow": payload},
        occurred_at=event_ts,
        camera_id=recommendation.camera_id,
    )
    logger.info(
        "%s recommendation_id=%s camera_id=%s status_before=%s status_after=%s created=%s event_id=%s",
        event_type,
        recommendation.recommendation_id,
        recommendation.camera_id,
        status_before,
        status_after,
        created,
        timeline_event.source_event_id,
    )
    logger.debug(
        "camera_recommendation_workflow_payload recommendation_id=%s event_type=%s payload=%s",
        recommendation.recommendation_id,
        event_type,
        payload,
    )


def workflow_states_by_recommendation(session: Session) -> dict[str, RecommendationWorkflowState]:
    states: dict[str, RecommendationWorkflowState] = {}
    rows = list_timeline_rows(
        session,
        event_types=RECOMMENDATION_WORKFLOW_EVENT_TYPES,
        descending=False,
    )
    for row in rows:
        workflow_payload = (row.payload or {}).get("camera_recommendation_workflow")
        if not isinstance(workflow_payload, dict):
            continue
        recommendation_id = workflow_payload.get("recommendation_id")
        status_after = workflow_payload.get("status_after")
        if not recommendation_id or status_after not in RECOMMENDATION_STATUSES:
            continue
        action_event = (row.payload or {}).get("action_event")
        action_event_id = action_event.get("event_id") if isinstance(action_event, dict) else None
        result = workflow_payload.get("result") if isinstance(workflow_payload.get("result"), dict) else {}
        states[str(recommendation_id)] = RecommendationWorkflowState(
            status=str(status_after),
            event_type=row.event_type,
            event_id=str(action_event_id or row.source_event_id),
            actor=_optional_text(workflow_payload.get("actor")),
            actor_user_id=_optional_text(workflow_payload.get("actor_user_id")),
            comment=_optional_text(workflow_payload.get("comment")),
            occurred_at=_optional_text(workflow_payload.get("occurred_at")) or row.occurred_at.isoformat(),
            result=result,
            payload=dict(workflow_payload),
        )
    return states


def read_persisted_recommendations(
    source_path: str | Path,
    *,
    max_records: int | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in recommendation_files(source_path):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("camera_recommendation_invalid_json path=%s line=%s", path, line_number)
                        continue
                    if isinstance(parsed, dict):
                        normalized = normalize_recommendation_record(parsed)
                        if normalized is not None:
                            records.append(normalized)
        except FileNotFoundError:
            continue

    if max_records is not None and max_records > 0:
        records = records[-max_records:]

    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        by_id[str(record["recommendation_id"])] = record
    return list(by_id.values())


def recommendation_files(source_path: str | Path) -> list[Path]:
    path = resolve_recommendations_path(source_path)
    rotated = sorted(
        [
            candidate
            for candidate in path.parent.glob(f"{path.stem}.*{path.suffix}")
            if candidate.is_file()
        ],
        key=lambda item: (item.stat().st_mtime, item.name),
    )
    files = list(rotated)
    if path.exists() and path.is_file():
        files.append(path)
    return files


def resolve_recommendations_path(source_path: str | Path) -> Path:
    path = Path(source_path)
    if path.suffix.lower() in {".jsonl", ".ndjson", ".log"}:
        return path
    return path / DEFAULT_RECOMMENDATIONS_FILENAME


def normalize_recommendation_record(record: dict[str, Any]) -> dict[str, Any] | None:
    camera_id = _optional_text(record.get("camera_id"))
    recommendation_type = _optional_text(record.get("recommendation_type"))
    if not camera_id or not recommendation_type:
        return None
    normalized = dict(record)
    normalized["recommendation_id"] = _optional_text(record.get("recommendation_id")) or _synthetic_recommendation_id(record)
    normalized["camera_id"] = camera_id
    normalized["recommendation_type"] = recommendation_type
    source_status = _optional_text(record.get("status"))
    normalized["status"] = source_status if source_status in RECOMMENDATION_STATUSES else RECOMMENDATION_STATUS_PENDING
    normalized["actionable"] = bool(record.get("actionable", False))
    normalized["auto_apply"] = bool(record.get("auto_apply", False))
    return normalized


def infer_metadata_paths(recommendation_type: str, suggested_value: Any, actionable: bool) -> list[str]:
    if not actionable or not isinstance(suggested_value, dict):
        return []
    if recommendation_type == "face_tuning":
        return [
            f"api.camera.metadata.recognition.face_tuning.{key}"
            for key in sorted(suggested_value)
            if key in FACE_TUNING_PATCH_FIELDS
        ]
    if recommendation_type in {"vlm_policy", "budget", "event_policy"}:
        paths: list[str] = []
        for key in sorted(suggested_value):
            canonical = VLM_POLICY_FIELD_ALIASES.get(key, key)
            if canonical in VLM_POLICY_PATCH_FIELDS:
                paths.append(f"api.camera.metadata.recognition.vlm_policy.{canonical}")
        return paths
    return []


def _to_read(record: dict[str, Any], state: RecommendationWorkflowState | None) -> CameraRecommendationRead:
    source_status = _optional_text(record.get("status"))
    status = state.status if state else (source_status if source_status in RECOMMENDATION_STATUSES else RECOMMENDATION_STATUS_PENDING)
    metadata_paths = infer_metadata_paths(
        str(record.get("recommendation_type")),
        record.get("suggested_value"),
        bool(record.get("actionable")),
    )
    result = state.result if state else {}
    last_error = None
    if isinstance(result, dict):
        last_error = _optional_text(result.get("error")) or _optional_text(result.get("detail"))
    workflow = {}
    if state is not None:
        workflow = {
            "last_event_type": state.event_type,
            "last_event_id": state.event_id,
            "actor": state.actor,
            "actor_user_id": state.actor_user_id,
            "comment": state.comment,
            "occurred_at": state.occurred_at,
            "result": state.result or {},
        }
    return CameraRecommendationRead(
        recommendation_id=str(record["recommendation_id"]),
        camera_id=str(record["camera_id"]),
        status=status,  # type: ignore[arg-type]
        recommendation_type=str(record["recommendation_type"]),
        current_value=record.get("current_value"),
        suggested_value=record.get("suggested_value"),
        evidence=dict(record.get("evidence") or {}),
        generated_at=_optional_text(record.get("generated_at")),
        actionable=bool(record.get("actionable")),
        applicable=bool(metadata_paths),
        metadata_paths=metadata_paths,
        impact=_impact_text(str(record.get("recommendation_type")), metadata_paths),
        severity=_optional_text(record.get("severity")),
        title=_optional_text(record.get("title")),
        reason=_optional_text(record.get("reason")),
        confidence=_optional_float(record.get("confidence")),
        metrics_used=_string_list(record.get("metrics_used")),
        window_summary=dict(record.get("window_summary") or {}),
        rule_set_version=_optional_text(record.get("rule_set_version")),
        source_status=source_status,
        auto_apply=bool(record.get("auto_apply", False)),
        workflow=workflow,
        last_error=last_error,
    )


def _workflow_summary(event_type: str, recommendation: CameraRecommendationRead) -> str:
    action = {
        RECOMMENDATION_EVENT_APPROVED: "aprobada",
        RECOMMENDATION_EVENT_REJECTED: "rechazada",
        RECOMMENDATION_EVENT_APPLIED: "aplicada",
        RECOMMENDATION_EVENT_FAILED: "fallida",
        RECOMMENDATION_EVENT_ROLLED_BACK: "revertida",
    }.get(event_type, "actualizada")
    return f"Recomendacion de camara {action}: {recommendation.recommendation_type}"


def _impact_text(recommendation_type: str, metadata_paths: list[str]) -> str | None:
    if not metadata_paths:
        return "No tiene patch automatico seguro; queda solo para revision humana."
    if recommendation_type == "face_tuning":
        return "Ajusta tuning facial por camara y se propagara como config viva en la siguiente corrida."
    if recommendation_type in {"vlm_policy", "budget", "event_policy"}:
        return "Ajusta policy VLM por camara y se propagara como config viva en la siguiente corrida."
    return "Aplica un cambio puntual en metadata.recognition."


def _synthetic_recommendation_id(record: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "camera_id": record.get("camera_id"),
            "recommendation_type": record.get("recommendation_type"),
            "generated_at": record.get("generated_at"),
            "suggested_value": record.get("suggested_value"),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"synthetic:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _require_actor(actor: str) -> None:
    if not str(actor or "").strip():
        raise WorkflowValidationError("actor is required")
