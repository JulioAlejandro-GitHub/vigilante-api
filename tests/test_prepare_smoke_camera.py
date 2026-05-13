from __future__ import annotations

from argparse import Namespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import Camera
from scripts.prepare_smoke_camera import (
    DEFAULT_CAMERA_ID,
    DEFAULT_EXTERNAL_CAMERA_KEY,
    SmokeCameraSetupError,
    build_parser,
    build_smoke_metadata,
    prepare_smoke_camera,
)
from tests.conftest import SITE_1, SITE_2


def test_prepare_smoke_camera_creates_visible_active_camera(auth_headers, tmp_path) -> None:
    state_file = tmp_path / "smoke-camera.env"
    result = prepare_smoke_camera(_args("--state-file", str(state_file)))

    assert result.camera_id == str(DEFAULT_CAMERA_ID)
    assert result.external_camera_key == DEFAULT_EXTERNAL_CAMERA_KEY
    assert result.site_id == SITE_1
    assert result.source_type == "rtsp"
    assert result.is_active is True
    assert "VIGILANTE_RECOMMENDATION_CAMERA_ID=11111111-1111-1111-1111-111111111111" in state_file.read_text()

    client = TestClient(app)
    visible = client.get(f"/api/v1/cameras/{result.camera_id}", headers=auth_headers("julio"))
    assert visible.status_code == 200
    body = visible.json()
    assert body["metadata"]["smoke"]["is_smoke_ready"] is True
    assert body["metadata"]["recognition"]["face_tuning"]["face_quality_threshold"] == 0.75
    assert body["is_active"] is True

    denied = client.get(f"/api/v1/cameras/{result.camera_id}", headers=auth_headers("ana"))
    assert denied.status_code == 403


def test_prepare_smoke_camera_reuses_existing_camera_and_repairs_scope_and_ingestion_fields() -> None:
    existing_id = UUID("44444444-4444-4444-4444-444444444444")
    with get_session() as session:
        session.add(
            Camera(
                camera_id=existing_id,
                external_camera_key=DEFAULT_EXTERNAL_CAMERA_KEY,
                site_id=UUID(SITE_2),
                is_active=False,
                source_type="file_replay",
                camera_metadata={"notes": "existing smoke camera"},
            )
        )
        session.commit()

    result = prepare_smoke_camera(_args())

    assert result.camera_id == str(existing_id)
    assert result.action == "updated_existing"
    with get_session() as session:
        camera = session.get(Camera, existing_id)
        assert camera is not None
        assert str(camera.site_id) == SITE_1
        assert camera.is_active is True
        assert camera.source_type == "rtsp"
        assert camera.camera_hostname == "127.0.0.1"
        assert camera.camera_path == "/cam01"
        assert camera.camera_metadata["notes"] == "existing smoke camera"
        assert camera.camera_metadata["smoke"]["is_smoke_ready"] is True


def test_prepare_smoke_camera_fails_when_requested_site_is_not_visible_to_smoke_user() -> None:
    with pytest.raises(SmokeCameraSetupError) as exc:
        prepare_smoke_camera(_args("--site-id", SITE_2))

    assert exc.value.code == "smoke_camera_permission_mismatch"


def test_build_smoke_metadata_preserves_existing_recognition_values() -> None:
    metadata = build_smoke_metadata(
        {"recognition": {"face_tuning": {"face_quality_threshold": 0.62}}},
        external_camera_key="smoke",
        rtsp_url="rtsp://127.0.0.1:8554/cam01",
    )

    assert metadata["recognition"]["face_tuning"]["face_quality_threshold"] == 0.62
    assert metadata["recognition"]["vlm_policy"]["backend"] == "simple"
    assert metadata["smoke"]["is_smoke_ready"] is True


def _args(*extra: str) -> Namespace:
    return build_parser().parse_args([*extra, "--skip-ingestion-schema-check"])
