from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_session
from app.main import app
from app.models import Camera
from app.services.camera_secret_service import decrypt_camera_secret
from tests.conftest import SITE_1


CAMERA_ID = UUID("33333333-3333-3333-3333-333333333333")


def test_camera_endpoints_do_not_expose_secret_or_legacy_secret_metadata(auth_headers) -> None:
    with get_session() as session:
        session.add(
            Camera(
                camera_id=CAMERA_ID,
                external_camera_key="cam-rtsp",
                site_id=UUID(SITE_1),
                source_type="rtsp",
                camera_hostname="192.168.100.143",
                camera_port=554,
                camera_path="/cam/realmonitor",
                rtsp_transport="tcp",
                channel=1,
                subtype=0,
                camera_user="admin",
                camera_secret="admin123",
                camera_metadata={
                    "camera_pass": "admin123",
                    "stream_url": "rtsp://admin:admin123@192.168.100.143:554/cam/realmonitor?channel=1&subtype=0",
                    "notes": "legacy metadata with credentials",
                },
            )
        )
        session.commit()

    client = TestClient(app)
    client.headers.update(auth_headers())

    list_response = client.get("/api/v1/cameras")
    assert list_response.status_code == 200
    [camera] = list_response.json()

    assert "camera_secret" not in camera
    assert camera["camera_user"] == "admin"
    assert camera["camera_hostname"] == "192.168.100.143"
    assert camera["metadata"]["stream_url"] == "rtsp://admin:***@192.168.100.143:554/cam/realmonitor?channel=1&subtype=0"
    assert "camera_pass" not in camera["metadata"]
    assert "admin123" not in str(camera)

    detail_response = client.get(f"/api/v1/cameras/{CAMERA_ID}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert "camera_secret" not in detail
    assert "admin123" not in str(detail)


def test_camera_secret_is_encrypted_when_persisted() -> None:
    with get_session() as session:
        session.add(
            Camera(
                camera_id=CAMERA_ID,
                site_id=UUID(SITE_1),
                source_type="rtsp",
                camera_hostname="192.168.100.143",
                camera_path="/cam/realmonitor",
                camera_user="admin",
                camera_secret="admin123",
            )
        )
        session.commit()
        stored = session.execute(
            text("SELECT camera_secret FROM camera WHERE camera_id = :camera_id"),
            {"camera_id": str(CAMERA_ID).replace("-", "")},
        ).scalar_one()

    assert stored != "admin123"
    assert stored.startswith("fernet:v1:")
    assert "admin123" not in stored
    assert decrypt_camera_secret(stored) == "admin123"
