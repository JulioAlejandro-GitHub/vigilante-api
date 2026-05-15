from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.events import ingest_event
from app.services.media_models import MediaAssetResponse
from tests.helpers.fixtures import load_fixture_event


class _CountingMediaClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, ref: str) -> MediaAssetResponse:
        self.calls.append(ref)
        return MediaAssetResponse(
            media_id=f"media-{len(self.calls)}",
            storage_backend="local",
            object_key=ref.rsplit("/", 1)[-1],
            content_type="image/jpeg",
            source_ref=ref,
            content_url=f"/api/v1/media/media-{len(self.calls)}/content",
            thumbnail_url=f"/api/v1/media/media-{len(self.calls)}/thumbnail",
            thumbnail_available=True,
            thumbnail_status="available",
        )


def test_timeline_list_is_paged_and_skips_media_by_default(auth_headers, caplog) -> None:
    media_client = _CountingMediaClient()
    app.dependency_overrides[evidence_resolution_service_dependency] = lambda: EvidenceResolutionService(client=media_client)
    try:
        with get_session() as session:
            ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
            ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))

        client = TestClient(app)
        client.headers.update(auth_headers())

        with caplog.at_level(logging.INFO):
            first_page = client.get("/api/v1/timeline", params={"limit": 1, "offset": 0}).json()

        assert len(first_page) == 1
        assert first_page[0]["evidence_media"] == []
        assert media_client.calls == []
        info_text = "\n".join(record.getMessage() for record in caplog.records if record.levelno == logging.INFO)
        assert "timeline_loaded items=1 limit=1 offset=0 include_evidence=False media_requested=0 media_resolved=0" in info_text
        assert "media_resolve_requested" not in info_text

        second_page = client.get("/api/v1/timeline", params={"limit": 1, "offset": 1}).json()
        assert len(second_page) == 1
        assert second_page[0]["source_event_id"] != first_page[0]["source_event_id"]
    finally:
        app.dependency_overrides.pop(evidence_resolution_service_dependency, None)


def test_timeline_evidence_endpoint_resolves_only_requested_page(auth_headers, caplog) -> None:
    media_client = _CountingMediaClient()
    app.dependency_overrides[evidence_resolution_service_dependency] = lambda: EvidenceResolutionService(client=media_client)
    try:
        with get_session() as session:
            ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))

        client = TestClient(app)
        client.headers.update(auth_headers())

        with caplog.at_level(logging.INFO):
            response = client.get("/api/v1/timeline/evt_rec_case_suggestion_created_001/evidence", params={"limit": 1})

        assert response.status_code == 200
        payload = response.json()
        assert payload["limit"] == 1
        assert payload["offset"] == 0
        assert len(payload["items"]) == 1
        assert len(media_client.calls) == 1
        info_text = "\n".join(record.getMessage() for record in caplog.records if record.levelno == logging.INFO)
        assert "timeline_evidence_loaded source_event_id=evt_rec_case_suggestion_created_001 items=1" in info_text
        assert "media_resolved=1" in info_text
        assert "media_resolve_requested" not in info_text
    finally:
        app.dependency_overrides.pop(evidence_resolution_service_dependency, None)
