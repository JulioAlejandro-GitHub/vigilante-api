from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from app.config import reset_settings_cache
from app.db import get_session
from app.main import app
from app.models import Camera
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.media_models import MediaAssetResponse
from tests.conftest import SITE_1


CAMERA_ID = UUID("44444444-4444-4444-4444-444444444444")


class _FakeMediaClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, ref: str) -> MediaAssetResponse:
        self.calls.append(ref)
        return MediaAssetResponse(
            media_id="media-latest-frame",
            media_type="frame",
            storage_backend="local",
            content_type="image/jpeg",
            size_bytes=1234,
            captured_at=datetime.now(timezone.utc),
            camera_id=str(CAMERA_ID),
            metadata={"width": 1280, "height": 720},
            content_url=f"/api/v1/media/media-latest-frame/content",
            thumbnail_url=f"/api/v1/media/media-latest-frame/thumbnail",
            thumbnail_available=True,
            thumbnail_status="available",
            metadata_url=f"/api/v1/media/media-latest-frame",
        )


def test_latest_frames_returns_ingestion_frame_before_recognition(auth_headers, tmp_path, monkeypatch) -> None:
    outbox = tmp_path / "frame_ingested.jsonl"
    older_at = datetime.now(timezone.utc) - timedelta(seconds=12)
    latest_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    older_ref = str(tmp_path / "older.jpg")
    latest_ref = str(tmp_path / "latest.jpg")
    outbox.write_text(
        "\n".join(
            [
                json.dumps(_frame_event("evt_old", older_at, older_ref)),
                json.dumps(_frame_event("evt_latest", latest_at, latest_ref)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with get_session() as session:
        session.add(
            Camera(
                camera_id=CAMERA_ID,
                external_camera_key="cam-live",
                site_id=UUID(SITE_1),
                name="Live camera",
                is_active=True,
                source_type="rtsp",
            )
        )
        session.commit()

    media_client = _FakeMediaClient()
    app.dependency_overrides[evidence_resolution_service_dependency] = lambda: EvidenceResolutionService(client=media_client)
    monkeypatch.setenv("INGESTION_OUTBOX_PATH", str(outbox))
    monkeypatch.setenv("INGESTION_HEALTH_BASE_URL", "")
    reset_settings_cache()
    try:
        client = TestClient(app)
        client.headers.update(auth_headers())
        response = client.get("/api/v1/cameras/latest-frames", params=[("camera_id", str(CAMERA_ID))])
    finally:
        app.dependency_overrides.pop(evidence_resolution_service_dependency, None)
        reset_settings_cache()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    frame = payload[0]
    assert frame["camera_id"] == str(CAMERA_ID)
    assert frame["latest_frame_ref"] == latest_ref
    assert frame["event_id"] == "evt_latest"
    assert frame["state"] == "live"
    assert frame["media"]["thumbnail_url"].endswith("/thumbnail")
    assert media_client.calls == [latest_ref]


def _frame_event(event_id: str, occurred_at: datetime, frame_ref: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "frame.ingested",
        "event_version": "1.0",
        "occurred_at": occurred_at.isoformat(),
        "emitted_at": occurred_at.isoformat(),
        "source": {"component": "vigilante-ingestion"},
        "context": {"camera_id": str(CAMERA_ID)},
        "payload": {
            "camera_id": str(CAMERA_ID),
            "captured_at": occurred_at.isoformat(),
            "content_type": "image/jpeg",
            "frame_ref": frame_ref,
            "frame_uri": frame_ref,
            "width": 1280,
            "height": 720,
            "metadata": {"storage_backend": "local", "sample_index": 1},
            "source_type": "rtsp",
        },
    }
