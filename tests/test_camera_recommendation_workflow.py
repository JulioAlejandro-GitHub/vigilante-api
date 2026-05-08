from __future__ import annotations

import json
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import reset_settings_cache
from app.db import get_session
from app.main import app
from app.models import Camera, TimelineEvent
from tests.conftest import SITE_1


CAMERA_ID = UUID("33333333-3333-3333-3333-333333333333")


def test_camera_recommendations_can_be_listed_approved_and_rejected(monkeypatch, tmp_path, auth_headers) -> None:
    _write_recommendations(
        monkeypatch,
        tmp_path,
        [
            _recommendation("rec-face-quality", {"face_quality_threshold": 0.65}),
            _recommendation("rec-informational", "keep_current_configuration", actionable=False, recommendation_type="status"),
        ],
    )
    _seed_camera()

    client = TestClient(app)
    client.headers.update(auth_headers())

    listed = client.get("/api/v1/camera-recommendations", params={"status": "pending"})
    assert listed.status_code == 200
    payload = listed.json()
    assert {item["recommendation_id"] for item in payload} == {"rec-face-quality", "rec-informational"}
    actionable = next(item for item in payload if item["recommendation_id"] == "rec-face-quality")
    assert actionable["status"] == "pending"
    assert actionable["evidence"]["face_usable_rate"] == 0.3
    assert actionable["suggested_value"]["face_quality_threshold"] == 0.65
    assert actionable["metadata_paths"] == [
        "api.camera.metadata.recognition.face_tuning.face_quality_threshold"
    ]

    approved = client.post("/api/v1/camera-recommendations/rec-face-quality/approve", json={"comment": "probar umbral menor"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["workflow"]["actor"] == "julio"

    rejected = client.post("/api/v1/camera-recommendations/rec-informational/reject", json={"comment": "solo informativa"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    rejected_apply = client.post("/api/v1/camera-recommendations/rec-informational/apply", json={})
    assert rejected_apply.status_code == 409

    with get_session() as session:
        event_types = session.scalars(select(TimelineEvent.event_type)).all()
        camera = session.get(Camera, CAMERA_ID)

    assert "camera_recommendation_approved" in event_types
    assert "camera_recommendation_rejected" in event_types
    assert camera.camera_metadata["recognition"]["face_tuning"]["face_quality_threshold"] == 0.75


def test_non_applicable_recommendation_can_be_previewed_but_not_applied(monkeypatch, tmp_path, auth_headers) -> None:
    _write_recommendations(
        monkeypatch,
        tmp_path,
        [_recommendation("rec-status", "insufficient_evidence", actionable=False, recommendation_type="status")],
    )
    _seed_camera()

    client = TestClient(app)
    client.headers.update(auth_headers())

    preview = client.get("/api/v1/camera-recommendations/rec-status/preview")
    assert preview.status_code == 200
    assert preview.json()["applicable"] is False
    assert preview.json()["validation_errors"] == ["recommendation_not_actionable"]

    approved = client.post("/api/v1/camera-recommendations/rec-status/approve", json={"comment": "registrar revision"})
    assert approved.status_code == 200
    apply_response = client.post("/api/v1/camera-recommendations/rec-status/apply", json={})
    assert apply_response.status_code == 422

    listed = client.get("/api/v1/camera-recommendations/rec-status")
    assert listed.status_code == 200
    assert listed.json()["status"] == "failed"
    assert "recommendation_not_actionable" in listed.json()["last_error"]


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


def _write_recommendations(monkeypatch, tmp_path, records: list[dict]) -> None:
    path = tmp_path / "recommendations.jsonl"
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    monkeypatch.setenv("RECOGNITION_RECOMMENDATIONS_PATH", str(path))
    reset_settings_cache()


def _recommendation(
    recommendation_id: str,
    suggested_value,
    *,
    actionable: bool = True,
    recommendation_type: str = "face_tuning",
) -> dict:
    return {
        "schema_version": "runtime_recommendation_v1",
        "rule_set_version": "runtime_recommendation_rules_v1",
        "recommendation_id": recommendation_id,
        "camera_id": str(CAMERA_ID),
        "status": "pending",
        "recommendation_type": recommendation_type,
        "severity": "medium",
        "title": "Ajustar face_quality_threshold",
        "reason": "La camara detecta rostros, pero muchos no quedan usables.",
        "evidence": {"face_usable_rate": 0.3},
        "current_value": {"face_quality_threshold": 0.75},
        "suggested_value": suggested_value,
        "confidence": 0.78,
        "metrics_used": ["face_usable_rate"],
        "window_summary": {"metrics_records_in_window": 20},
        "generated_at": "2026-05-07T12:00:00+00:00",
        "actionable": actionable,
        "auto_apply": False,
    }
