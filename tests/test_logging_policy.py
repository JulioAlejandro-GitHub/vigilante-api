from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.config import get_settings
from app.logging_config import current_log_level_name, set_log_level
from app.main import app
from app.services.evidence_resolution_service import EvidenceResolutionService
from app.services.media_models import MediaAssetResponse


class _FakeMediaClient:
    def resolve(self, ref: str) -> MediaAssetResponse:
        return MediaAssetResponse(
            media_id="media_123",
            storage_backend="s3",
            bucket="vigilante-frames",
            object_key=ref.rsplit("/", 1)[-1],
            content_type="image/jpeg",
            source_ref=ref,
            content_url="/content",
        )


def test_admin_log_level_endpoint_changes_runtime_level(auth_headers, tmp_path) -> None:
    settings = get_settings()
    previous_path = settings.runtime_log_level_path
    previous_level = current_log_level_name()
    settings.runtime_log_level_path = str(tmp_path / "log-level")
    client = TestClient(app)
    client.headers.update(auth_headers("maria"))

    try:
        response = client.post("/admin/log-level", json={"level": "DEBUG"})
        assert response.status_code == 200
        assert response.json()["level"] == "DEBUG"
        assert (tmp_path / "log-level").read_text(encoding="utf-8").strip() == "DEBUG"
        assert current_log_level_name() == "DEBUG"

        response = client.post("/admin/log-level", json={"level": "INFO"})
        assert response.status_code == 200
        assert current_log_level_name() == "INFO"
    finally:
        settings.runtime_log_level_path = previous_path
        set_log_level(previous_level, source="test_restore", announce=False)


def test_media_resolution_info_is_compact_and_debug_keeps_reference(caplog) -> None:
    ref = "s3://vigilante-frames/frames/camera-01/" + ("very-long-segment-" * 20) + "frame.jpg"
    service = EvidenceResolutionService(client=_FakeMediaClient())

    with caplog.at_level(logging.INFO):
        service.resolve_refs([ref])

    info_text = "\n".join(record.getMessage() for record in caplog.records if record.levelno == logging.INFO)
    assert "media_resolve_requested ref_hash=" in info_text
    assert "media_resolve_succeeded ref_hash=" in info_text
    assert "media_id=media_123" in info_text
    assert ref not in info_text

    caplog.clear()
    service = EvidenceResolutionService(client=_FakeMediaClient())
    with caplog.at_level(logging.DEBUG):
        service.resolve_refs([ref])

    debug_text = "\n".join(record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG)
    assert ref in debug_text
