from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models import Camera
from app.services.camera_recommendation_workflow_service import (
    FACE_TUNING_PATCH_FIELDS,
    RECOMMENDATION_EVENT_APPLIED,
    RECOMMENDATION_EVENT_FAILED,
    RECOMMENDATION_EVENT_ROLLED_BACK,
    RECOMMENDATION_STATUS_APPLIED,
    RECOMMENDATION_STATUS_APPROVED,
    RECOMMENDATION_STATUS_FAILED,
    RECOMMENDATION_STATUS_ROLLED_BACK,
    VLM_POLICY_FIELD_ALIASES,
    VLM_POLICY_PATCH_FIELDS,
    CameraRecommendationRead,
    get_recommendation,
    record_recommendation_workflow_event,
    workflow_states_by_recommendation,
)
from app.services.workflow_exceptions import WorkflowConflictError, WorkflowNotFoundError, WorkflowValidationError

MISSING = object()


class CameraRecommendationApplyRequest(BaseModel):
    comment: str | None = None


class CameraRecommendationRollbackRequest(BaseModel):
    comment: str | None = None


class RecommendationPatchPreview(BaseModel):
    metadata_path: str
    path: list[str]
    current_value: Any = None
    current_value_present: bool
    expected_current_value: Any = None
    expected_current_value_present: bool = False
    suggested_value: Any = None
    stale: bool = False


class CameraRecommendationPreview(BaseModel):
    recommendation: CameraRecommendationRead
    applicable: bool
    impact: str | None = None
    patches: list[RecommendationPatchPreview] = Field(default_factory=list)
    diff: dict[str, dict[str, Any]] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)


class CameraRecommendationApplyResult(BaseModel):
    recommendation: CameraRecommendationRead
    applied: bool
    patches: list[RecommendationPatchPreview] = Field(default_factory=list)
    metadata_hash_before: str | None = None
    metadata_hash_after: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class PatchOperation:
    path: tuple[str, ...]
    suggested_value: Any
    expected_current_value: Any = MISSING

    @property
    def metadata_path(self) -> str:
        return "api.camera.metadata." + ".".join(self.path)


def preview_recommendation(
    session: Session,
    recommendation_id: str,
    *,
    source_path: str | None = None,
) -> CameraRecommendationPreview:
    recommendation = get_recommendation(session, recommendation_id, source_path=source_path)
    errors: list[str] = []
    camera = _get_camera_row(session, recommendation.camera_id)
    if camera is None:
        errors.append("camera_not_found")
        return CameraRecommendationPreview(
            recommendation=recommendation,
            applicable=False,
            impact=recommendation.impact,
            validation_errors=errors,
        )

    plan, plan_errors = build_patch_plan(recommendation)
    errors.extend(plan_errors)
    metadata = deepcopy(camera.camera_metadata or {})
    patch_previews = [_preview_patch(metadata, operation) for operation in plan]
    for patch in patch_previews:
        if patch.stale:
            errors.append(f"stale_current_value:{patch.metadata_path}")

    return CameraRecommendationPreview(
        recommendation=recommendation,
        applicable=bool(plan) and not errors,
        impact=recommendation.impact,
        patches=patch_previews,
        diff={
            patch.metadata_path: {
                "from": patch.current_value if patch.current_value_present else None,
                "to": patch.suggested_value,
                "current_value_present": patch.current_value_present,
            }
            for patch in patch_previews
        },
        validation_errors=errors,
    )


def apply_recommendation(
    session: Session,
    recommendation_id: str,
    *,
    actor: str,
    actor_user_id: str | None,
    comment: str | None = None,
    source_path: str | None = None,
) -> CameraRecommendationApplyResult:
    recommendation = get_recommendation(session, recommendation_id, source_path=source_path)
    if recommendation.status != RECOMMENDATION_STATUS_APPROVED:
        raise WorkflowConflictError(f"Recommendation must be approved before apply; current status is {recommendation.status}")

    metadata_hash_before: str | None = None
    try:
        camera = _require_camera_row(session, recommendation.camera_id)
        metadata_before = deepcopy(camera.camera_metadata or {})
        metadata_hash_before = stable_hash(metadata_before.get("recognition") or {})
        plan, errors = build_patch_plan(recommendation)
        if errors:
            raise WorkflowValidationError("; ".join(errors))
        if not plan:
            raise WorkflowValidationError("Recommendation is not applicable")

        previews = [_preview_patch(metadata_before, operation) for operation in plan]
        stale_paths = [patch.metadata_path for patch in previews if patch.stale]
        if stale_paths:
            raise WorkflowValidationError("Recommendation is stale for " + ", ".join(stale_paths))

        metadata_after = deepcopy(metadata_before)
        for operation in plan:
            _set_path(metadata_after, operation.path, deepcopy(operation.suggested_value))
        metadata_hash_after = stable_hash(metadata_after.get("recognition") or {})
        camera.camera_metadata = metadata_after

        applied_patches = [patch.model_dump(mode="json") for patch in previews]
        application = {
            "patches": applied_patches,
            "metadata_hash_before": metadata_hash_before,
            "metadata_hash_after": metadata_hash_after,
            "recognition_version_before": _recognition_version(metadata_before),
            "recognition_version_after": _recognition_version(metadata_after),
        }
        record_recommendation_workflow_event(
            session,
            recommendation=recommendation,
            event_type=RECOMMENDATION_EVENT_APPLIED,
            status_before=recommendation.status,
            status_after=RECOMMENDATION_STATUS_APPLIED,
            actor=actor,
            actor_user_id=actor_user_id,
            comment=comment,
            result={"status": "applied", "patch_count": len(applied_patches)},
            application=application,
        )
        session.commit()
        updated = get_recommendation(session, recommendation_id, source_path=source_path)
        return CameraRecommendationApplyResult(
            recommendation=updated,
            applied=True,
            patches=previews,
            metadata_hash_before=metadata_hash_before,
            metadata_hash_after=metadata_hash_after,
        )
    except WorkflowValidationError as exc:
        _record_failed_apply(
            session,
            recommendation=recommendation,
            actor=actor,
            actor_user_id=actor_user_id,
            comment=comment,
            error=str(exc),
            metadata_hash_before=metadata_hash_before,
        )
        raise


def rollback_recommendation(
    session: Session,
    recommendation_id: str,
    *,
    actor: str,
    actor_user_id: str | None,
    comment: str | None = None,
    source_path: str | None = None,
) -> CameraRecommendationApplyResult:
    recommendation = get_recommendation(session, recommendation_id, source_path=source_path)
    if recommendation.status != RECOMMENDATION_STATUS_APPLIED:
        raise WorkflowConflictError(f"Recommendation must be applied before rollback; current status is {recommendation.status}")

    applied_payload = _latest_applied_payload(session, recommendation_id)
    if applied_payload is None:
        raise WorkflowNotFoundError("Applied recommendation audit payload not found")
    application = applied_payload.get("application") if isinstance(applied_payload.get("application"), dict) else {}
    raw_patches = application.get("patches") if isinstance(application.get("patches"), list) else []
    if not raw_patches:
        raise WorkflowValidationError("Applied recommendation does not contain rollback patch data")

    camera = _require_camera_row(session, recommendation.camera_id)
    metadata_before = deepcopy(camera.camera_metadata or {})
    metadata_hash_before = stable_hash(metadata_before.get("recognition") or {})
    rollback_previews: list[RecommendationPatchPreview] = []
    metadata_after = deepcopy(metadata_before)

    for raw_patch in raw_patches:
        if not isinstance(raw_patch, dict):
            raise WorkflowValidationError("Invalid rollback patch payload")
        path = tuple(str(part) for part in raw_patch.get("path", []))
        if not path:
            raise WorkflowValidationError("Invalid rollback patch path")
        applied_value = raw_patch.get("suggested_value")
        current_value = _get_path(metadata_before, path)
        if current_value is MISSING or current_value != applied_value:
            raise WorkflowValidationError("Current metadata no longer matches applied value for " + "api.camera.metadata." + ".".join(path))
        previous_present = bool(raw_patch.get("current_value_present"))
        previous_value = raw_patch.get("current_value")
        rollback_previews.append(
            RecommendationPatchPreview(
                metadata_path="api.camera.metadata." + ".".join(path),
                path=list(path),
                current_value=current_value,
                current_value_present=True,
                suggested_value=previous_value if previous_present else None,
            )
        )
        if previous_present:
            _set_path(metadata_after, path, deepcopy(previous_value))
        else:
            _delete_path(metadata_after, path)

    metadata_hash_after = stable_hash(metadata_after.get("recognition") or {})
    camera.camera_metadata = metadata_after
    rollback_payload = {
        "patches": [patch.model_dump(mode="json") for patch in rollback_previews],
        "metadata_hash_before": metadata_hash_before,
        "metadata_hash_after": metadata_hash_after,
        "restored_from_event_id": applied_payload.get("event_id"),
    }
    record_recommendation_workflow_event(
        session,
        recommendation=recommendation,
        event_type=RECOMMENDATION_EVENT_ROLLED_BACK,
        status_before=recommendation.status,
        status_after=RECOMMENDATION_STATUS_ROLLED_BACK,
        actor=actor,
        actor_user_id=actor_user_id,
        comment=comment,
        result={"status": "rolled_back", "patch_count": len(rollback_previews)},
        rollback=rollback_payload,
    )
    session.commit()
    updated = get_recommendation(session, recommendation_id, source_path=source_path)
    return CameraRecommendationApplyResult(
        recommendation=updated,
        applied=False,
        patches=rollback_previews,
        metadata_hash_before=metadata_hash_before,
        metadata_hash_after=metadata_hash_after,
    )


def build_patch_plan(recommendation: CameraRecommendationRead) -> tuple[list[PatchOperation], list[str]]:
    errors: list[str] = []
    if not recommendation.actionable:
        return [], ["recommendation_not_actionable"]
    if recommendation.auto_apply:
        return [], ["auto_apply_recommendations_are_not_supported"]
    if recommendation.recommendation_type not in {"face_tuning", "vlm_policy", "budget", "event_policy"}:
        return [], [f"recommendation_type_not_applicable:{recommendation.recommendation_type}"]
    if not isinstance(recommendation.suggested_value, dict):
        return [], ["suggested_value_must_be_object"]

    plan: list[PatchOperation] = []
    for raw_key, raw_value in recommendation.suggested_value.items():
        key = str(raw_key)
        if recommendation.recommendation_type == "face_tuning":
            if key not in FACE_TUNING_PATCH_FIELDS:
                errors.append(f"unsupported_face_tuning_field:{key}")
                continue
            value, error = _validate_face_tuning_value(key, raw_value)
            if error:
                errors.append(error)
                continue
            plan.append(
                PatchOperation(
                    path=("recognition", "face_tuning", key),
                    suggested_value=value,
                    expected_current_value=_expected_current_value(recommendation.current_value, key),
                )
            )
            continue

        canonical_key = VLM_POLICY_FIELD_ALIASES.get(key, key)
        if canonical_key not in VLM_POLICY_PATCH_FIELDS:
            errors.append(f"unsupported_vlm_policy_field:{key}")
            continue
        value, error = _validate_vlm_policy_value(canonical_key, raw_value)
        if error:
            errors.append(error)
            continue
        plan.append(
            PatchOperation(
                path=("recognition", "vlm_policy", canonical_key),
                suggested_value=value,
                expected_current_value=_expected_current_value(recommendation.current_value, canonical_key, key),
            )
        )
    return plan, errors


def stable_hash(value: Any) -> str | None:
    if value in (None, {}, []):
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_failed_apply(
    session: Session,
    *,
    recommendation: CameraRecommendationRead,
    actor: str,
    actor_user_id: str | None,
    comment: str | None,
    error: str,
    metadata_hash_before: str | None,
) -> None:
    record_recommendation_workflow_event(
        session,
        recommendation=recommendation,
        event_type=RECOMMENDATION_EVENT_FAILED,
        status_before=recommendation.status,
        status_after=RECOMMENDATION_STATUS_FAILED,
        actor=actor,
        actor_user_id=actor_user_id,
        comment=comment,
        result={
            "status": "failed",
            "error": error,
            "metadata_hash_before": metadata_hash_before,
        },
        occurred_at=datetime.now(timezone.utc),
    )
    session.commit()


def _preview_patch(metadata: dict[str, Any], operation: PatchOperation) -> RecommendationPatchPreview:
    current = _get_path(metadata, operation.path)
    current_present = current is not MISSING
    expected_present = operation.expected_current_value is not MISSING
    stale = bool(expected_present and current_present and current != operation.expected_current_value)
    return RecommendationPatchPreview(
        metadata_path=operation.metadata_path,
        path=list(operation.path),
        current_value=None if current is MISSING else deepcopy(current),
        current_value_present=current_present,
        expected_current_value=None if operation.expected_current_value is MISSING else deepcopy(operation.expected_current_value),
        expected_current_value_present=expected_present,
        suggested_value=deepcopy(operation.suggested_value),
        stale=stale,
    )


def _get_camera_row(session: Session, camera_id: str) -> Camera | None:
    try:
        parsed_camera_id = UUID(str(camera_id))
    except ValueError:
        return None
    return session.get(Camera, parsed_camera_id)


def _require_camera_row(session: Session, camera_id: str) -> Camera:
    camera = _get_camera_row(session, camera_id)
    if camera is None:
        raise WorkflowValidationError("camera_not_found")
    return camera


def _get_path(metadata: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = metadata
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return MISSING
        current = current[key]
    return current


def _set_path(metadata: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current: Any = metadata
    for key in path[:-1]:
        if key not in current:
            current[key] = {}
        if not isinstance(current[key], dict):
            raise WorkflowValidationError("metadata_parent_not_object:api.camera.metadata." + ".".join(path[:-1]))
        current = current[key]
    current[path[-1]] = value


def _delete_path(metadata: dict[str, Any], path: tuple[str, ...]) -> None:
    current: Any = metadata
    for key in path[:-1]:
        if not isinstance(current, dict) or key not in current:
            return
        current = current[key]
    if isinstance(current, dict):
        current.pop(path[-1], None)


def _expected_current_value(current_value: Any, *keys: str) -> Any:
    if not isinstance(current_value, dict):
        return MISSING
    for key in keys:
        if key in current_value:
            return deepcopy(current_value[key])
    return MISSING


def _validate_face_tuning_value(key: str, value: Any) -> tuple[Any, str | None]:
    if key == "det_size":
        return value, None if _valid_det_size(value) else "det_size_invalid"
    if key in {"detection_threshold", "face_quality_threshold", "min_face_area_ratio"}:
        parsed = _optional_float(value)
        if parsed is None or parsed < 0 or parsed > 1:
            return value, f"{key}_invalid"
        return parsed, None
    if key in {"max_faces", "min_face_bbox_size"}:
        parsed = _optional_int(value)
        if parsed is None or parsed < 0:
            return value, f"{key}_invalid"
        if key == "max_faces" and parsed == 0:
            return value, f"{key}_invalid"
        return parsed, None
    return value, f"unsupported_face_tuning_field:{key}"


def _validate_vlm_policy_value(key: str, value: Any) -> tuple[Any, str | None]:
    if key in {"enabled", "force_simple"}:
        parsed = _optional_bool(value)
        if parsed is None:
            return value, f"{key}_invalid"
        return parsed, None
    if key == "backend":
        normalized = str(value).strip().lower()
        if normalized not in {"auto", "simple", "qwen", "qwen_vl", "smolvlm"}:
            return value, "backend_invalid"
        return normalized, None
    if key in {"preferred_backend", "secondary_backend"}:
        normalized = str(value).strip().lower()
        if normalized not in {"qwen", "qwen_vl", "smolvlm"}:
            return value, f"{key}_invalid"
        return "qwen" if normalized == "qwen_vl" else normalized, None
    if key in {"enabled_event_types", "disabled_event_types"}:
        if not isinstance(value, list) or any(not str(item).strip() for item in value):
            return value, f"{key}_invalid"
        return [str(item).strip().lower() for item in value], None
    if key in {"max_allowed_latency_seconds", "max_allowed_rss_mb", "qwen_max_allowed_rss_mb", "smolvlm_max_allowed_rss_mb"}:
        parsed = _optional_float(value)
        if parsed is None or parsed < 0:
            return value, f"{key}_invalid"
        return parsed, None
    if key == "max_concurrent_inferences":
        parsed = _optional_int(value)
        if parsed is None or parsed < 0:
            return value, "max_concurrent_inferences_invalid"
        return parsed, None
    if key == "degradation_policy":
        normalized = str(value).strip().lower()
        if normalized not in {"auto_then_secondary_then_simple", "preferred_then_secondary_then_simple", "preferred_then_simple", "simple_only"}:
            return value, "degradation_policy_invalid"
        return normalized, None
    return value, f"unsupported_vlm_policy_field:{key}"


def _valid_det_size(value: Any) -> bool:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return all(_positive_int(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.lower().replace("x", ",")
    parts = [part.strip() for part in normalized.split(",", 1)]
    return len(parts) == 2 and all(_positive_int(part) for part in parts)


def _positive_int(value: Any) -> bool:
    parsed = _optional_int(value)
    return parsed is not None and parsed > 0


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _recognition_version(metadata: dict[str, Any]) -> str | None:
    recognition = metadata.get("recognition")
    if not isinstance(recognition, dict):
        return None
    version = recognition.get("config_version") or recognition.get("version")
    return str(version) if version not in (None, "") else None


def _latest_applied_payload(session: Session, recommendation_id: str) -> dict[str, Any] | None:
    state = workflow_states_by_recommendation(session).get(recommendation_id)
    if state is None or state.status != RECOMMENDATION_STATUS_APPLIED:
        return None
    payload = state.payload or {}
    payload = dict(payload)
    payload["event_id"] = state.event_id
    return payload
