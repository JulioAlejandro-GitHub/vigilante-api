from __future__ import annotations

import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings_cache
from app.db import get_session
from app.main import app
from app.models import Camera
from app.services.camera_recommendation_apply_service import apply_recommendation
from app.services.camera_recommendation_workflow_service import approve_recommendation, get_recommendation
from app.services.workflow_exceptions import WorkflowValidationError
from tests.conftest import ANALYST_USER_ID, SITE_1


CAMERA_ID = UUID("33333333-3333-3333-3333-333333333333")
MISSING_CAMERA_ID = UUID("33333333-3333-3333-3333-333333333399")


def test_apply_updates_only_target_metadata_field_and_rollback_restores_previous_value(monkeypatch, tmp_path, auth_headers) -> None:
    _write_recommendations(
        monkeypatch,
        tmp_path,
        [_recommendation("rec-face-quality", str(CAMERA_ID), {"face_quality_threshold": 0.65})],
    )
    _seed_camera()

    client = TestClient(app)
    client.headers.update(auth_headers())

    preview = client.get("/api/v1/camera-recommendations/rec-face-quality/preview")
    assert preview.status_code == 200
    assert preview.json()["applicable"] is True
    assert preview.json()["diff"]["api.camera.metadata.recognition.face_tuning.face_quality_threshold"]["from"] == 0.75
    assert preview.json()["diff"]["api.camera.metadata.recognition.face_tuning.face_quality_threshold"]["to"] == 0.65

    assert client.post("/api/v1/camera-recommendations/rec-face-quality/approve", json={"comment": "ok"}).status_code == 200
    applied = client.post("/api/v1/camera-recommendations/rec-face-quality/apply", json={"comment": "aplicar controlado"})
    assert applied.status_code == 200
    assert applied.json()["applied"] is True
    assert applied.json()["recommendation"]["status"] == "applied"
    assert applied.json()["metadata_hash_before"] != applied.json()["metadata_hash_after"]

    with get_session() as session:
        camera = session.get(Camera, CAMERA_ID)
        recognition = camera.camera_metadata["recognition"]
        assert recognition["face_tuning"]["face_quality_threshold"] == 0.65
        assert recognition["face_tuning"]["det_size"] == "640,640"
        assert recognition["vlm_policy"]["preferred_backend"] == "smolvlm"

    rolled_back = client.post("/api/v1/camera-recommendations/rec-face-quality/rollback", json={"comment": "volver al valor previo"})
    assert rolled_back.status_code == 200
    assert rolled_back.json()["recommendation"]["status"] == "rolled_back"

    with get_session() as session:
        camera = session.get(Camera, CAMERA_ID)
        assert camera.camera_metadata["recognition"]["face_tuning"]["face_quality_threshold"] == 0.75
        assert camera.camera_metadata["recognition"]["face_tuning"]["det_size"] == "640,640"


def test_apply_vlm_policy_recommendation_patches_only_requested_field(monkeypatch, tmp_path, auth_headers) -> None:
    _write_recommendations(
        monkeypatch,
        tmp_path,
        [
            _recommendation(
                "rec-vlm-preferred",
                str(CAMERA_ID),
                {"preferred_backend": "qwen", "qwen_max_allowed_rss_mb": 14336},
                recommendation_type="vlm_policy",
                current_value={"preferred_backend": "smolvlm", "qwen_max_allowed_rss_mb": 12288},
            )
        ],
    )
    _seed_camera()

    client = TestClient(app)
    client.headers.update(auth_headers())

    assert client.post("/api/v1/camera-recommendations/rec-vlm-preferred/approve", json={}).status_code == 200
    applied = client.post("/api/v1/camera-recommendations/rec-vlm-preferred/apply", json={})
    assert applied.status_code == 200
    assert applied.json()["patches"][0]["metadata_path"] == "api.camera.metadata.recognition.vlm_policy.preferred_backend"

    with get_session() as session:
        camera = session.get(Camera, CAMERA_ID)
        vlm_policy = camera.camera_metadata["recognition"]["vlm_policy"]
        assert vlm_policy["backend"] == "auto"
        assert vlm_policy["preferred_backend"] == "qwen"
        assert vlm_policy["qwen_max_allowed_rss_mb"] == 14336.0


def test_apply_fails_safely_when_recommendation_is_stale_or_patch_invalid(monkeypatch, tmp_path, auth_headers) -> None:
    _write_recommendations(
        monkeypatch,
        tmp_path,
        [
            _recommendation("rec-stale", str(CAMERA_ID), {"face_quality_threshold": 0.65}),
            _recommendation("rec-invalid", str(CAMERA_ID), {"face_quality_threshold": 1.5}),
        ],
    )
    _seed_camera(
        {
            "recognition": {
                "face_tuning": {
                    "det_size": "640,640",
                    "face_quality_threshold": 0.7,
                }
            }
        }
    )

    client = TestClient(app)
    client.headers.update(auth_headers())

    assert client.post("/api/v1/camera-recommendations/rec-stale/approve", json={}).status_code == 200
    stale = client.post("/api/v1/camera-recommendations/rec-stale/apply", json={})
    assert stale.status_code == 422
    assert "stale" in stale.json()["detail"]

    assert client.get("/api/v1/camera-recommendations/rec-stale").json()["status"] == "failed"
    with get_session() as session:
        camera = session.get(Camera, CAMERA_ID)
        assert camera.camera_metadata["recognition"]["face_tuning"]["face_quality_threshold"] == 0.7

    assert client.post("/api/v1/camera-recommendations/rec-invalid/approve", json={}).status_code == 200
    invalid = client.post("/api/v1/camera-recommendations/rec-invalid/apply", json={})
    assert invalid.status_code == 422
    assert "face_quality_threshold_invalid" in invalid.json()["detail"]


def test_apply_records_failure_when_camera_does_not_exist(monkeypatch, tmp_path) -> None:
    path = _write_recommendations(
        monkeypatch,
        tmp_path,
        [_recommendation("rec-missing-camera", str(MISSING_CAMERA_ID), {"face_quality_threshold": 0.65})],
    )

    with get_session() as session:
        approve_recommendation(
            session,
            "rec-missing-camera",
            actor="julio",
            actor_user_id=ANALYST_USER_ID,
            source_path=str(path),
        )
        with pytest.raises(WorkflowValidationError, match="camera_not_found"):
            apply_recommendation(
                session,
                "rec-missing-camera",
                actor="julio",
                actor_user_id=ANALYST_USER_ID,
                source_path=str(path),
            )
        failed = get_recommendation(session, "rec-missing-camera", source_path=str(path))

    assert failed.status == "failed"
    assert failed.last_error == "camera_not_found"


def _seed_camera(metadata: dict | None = None) -> None:
    with get_session() as session:
        session.add(
            Camera(
                camera_id=CAMERA_ID,
                site_id=UUID(SITE_1),
                source_type="rtsp",
                camera_metadata=metadata
                or {
                    "recognition": {
                        "version": "ops-test",
                        "face_tuning": {
                            "det_size": "640,640",
                            "face_quality_threshold": 0.75,
                        },
                        "vlm_policy": {
                            "backend": "auto",
                            "preferred_backend": "smolvlm",
                        },
                    }
                },
            )
        )
        session.commit()


def _write_recommendations(monkeypatch, tmp_path, records: list[dict]):
    path = tmp_path / "recommendations.jsonl"
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    monkeypatch.setenv("RECOGNITION_RECOMMENDATIONS_PATH", str(path))
    reset_settings_cache()
    return path


def _recommendation(
    recommendation_id: str,
    camera_id: str,
    suggested_value: dict,
    *,
    recommendation_type: str = "face_tuning",
    current_value: dict | None = None,
) -> dict:
    return {
        "schema_version": "runtime_recommendation_v1",
        "rule_set_version": "runtime_recommendation_rules_v1",
        "recommendation_id": recommendation_id,
        "camera_id": camera_id,
        "status": "pending",
        "recommendation_type": recommendation_type,
        "severity": "medium",
        "title": "Ajuste operativo",
        "reason": "Recomendacion de runtime metrics.",
        "evidence": {"sample": True},
        "current_value": current_value or {"face_quality_threshold": 0.75},
        "suggested_value": suggested_value,
        "confidence": 0.78,
        "metrics_used": ["runtime"],
        "window_summary": {"metrics_records_in_window": 20},
        "generated_at": "2026-05-07T12:00:00+00:00",
        "actionable": True,
        "auto_apply": False,
    }
