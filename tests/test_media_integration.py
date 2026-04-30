from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.case_record_service import PromoteCaseSuggestionRequest
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    list_case_suggestions,
    promote_case_suggestion,
    resolve_case_suggestion,
)
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.events import ingest_event
from app.services.media_client import MediaClient, MediaClientError
from app.services.media_models import MediaAssetResponse


def load_json_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class FakeMediaClient:
    def __init__(
        self,
        *,
        failures: dict[str, MediaClientError] | None = None,
        refs_without_thumbnail: set[str] | None = None,
    ) -> None:
        self.failures = failures or {}
        self.refs_without_thumbnail = refs_without_thumbnail or set()
        self.calls: list[str] = []

    def resolve(self, ref: str) -> MediaAssetResponse:
        self.calls.append(ref)
        if ref in self.failures:
            raise self.failures[ref]
        media_id = "media_" + str(abs(hash(ref)))
        thumbnail_url = None if ref in self.refs_without_thumbnail else f"http://media.local/api/v1/media/{media_id}/thumbnail"
        return MediaAssetResponse(
            media_id=media_id,
            media_type="frame",
            storage_backend="s3",
            bucket="vigilante-frames",
            object_key=ref.rsplit("/", 1)[-1],
            content_type="image/jpeg",
            size_bytes=1024,
            captured_at="2026-05-10T13:08:45.123Z",
            camera_id="11111111-1111-1111-1111-111111111104",
            metadata={"width": 640, "height": 480, "access_token": "must-not-leak"},
            source_ref=ref,
            content_url=f"http://media.local/api/v1/media/{media_id}/content",
            thumbnail_url=thumbnail_url,
            thumbnail_content_type="image/jpeg" if thumbnail_url else None,
            thumbnail_width=320 if thumbnail_url else None,
            thumbnail_height=240 if thumbnail_url else None,
            thumbnail_available=thumbnail_url is not None,
            thumbnail_status="available" if thumbnail_url else "unsupported",
            metadata_url=f"http://media.local/api/v1/media/{media_id}",
        )


def test_media_client_resolves_reference_and_normalizes_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/media/resolve"
        assert request.url.params["ref"] == "s3://vigilante-frames/frames/cam01/frame.jpg"
        return httpx.Response(
            200,
            json={
                "media_id": "media_abc",
                "media_type": "frame",
                "storage_backend": "s3",
                "bucket": "vigilante-frames",
                "object_key": "frames/cam01/frame.jpg",
                "content_type": "image/jpeg",
                "metadata": {"width": 320, "height": 240},
                "source_ref": "s3://vigilante-frames/frames/cam01/frame.jpg",
                "content_url": "/api/v1/media/media_abc/content",
                "thumbnail_url": "/api/v1/media/media_abc/thumbnail",
                "thumbnail_content_type": "image/jpeg",
                "thumbnail_width": 320,
                "thumbnail_height": 240,
                "thumbnail_available": True,
                "thumbnail_status": "available",
            },
        )

    client = MediaClient(
        base_url="http://media.internal",
        public_base_url="http://localhost:8100",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )

    asset = client.resolve("s3://vigilante-frames/frames/cam01/frame.jpg")

    assert asset.media_id == "media_abc"
    assert asset.content_url == "http://localhost:8100/api/v1/media/media_abc/content"
    assert asset.thumbnail_url == "http://localhost:8100/api/v1/media/media_abc/thumbnail"
    assert asset.thumbnail_content_type == "image/jpeg"
    assert asset.thumbnail_width == 320
    assert asset.thumbnail_height == 240
    assert asset.thumbnail_available is True
    assert asset.thumbnail_status == "available"
    assert asset.metadata_url == "http://localhost:8100/api/v1/media/media_abc"


def test_media_client_maps_media_errors() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": {"error": "remote_object_not_found", "message": "missing"}},
        )

    client = MediaClient(
        base_url="http://media.internal",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )

    try:
        client.resolve("s3://vigilante-frames/frames/missing.jpg")
    except MediaClientError as exc:
        assert exc.reason == "remote_object_not_found"
        assert exc.status_code == 404
    else:
        raise AssertionError("expected MediaClientError")


def test_evidence_resolution_success_and_metadata_sanitization() -> None:
    service = EvidenceResolutionService(client=FakeMediaClient())

    items = service.resolve_refs(["s3://vigilante-frames/frames/cam01/frame.jpg"])

    assert len(items) == 1
    assert items[0].resolved is True
    assert items[0].content_type == "image/jpeg"
    assert items[0].thumbnail_url is not None
    assert items[0].thumbnail_url.endswith("/thumbnail")
    assert items[0].thumbnail_content_type == "image/jpeg"
    assert items[0].thumbnail_width == 320
    assert items[0].thumbnail_height == 240
    assert items[0].thumbnail_available is True
    assert items[0].width == 640
    assert items[0].height == 480
    assert "access_token" not in items[0].metadata


def test_evidence_resolution_keeps_content_url_when_thumbnail_is_absent() -> None:
    ref = "s3://vigilante-frames/frames/cam01/frame.jpg"
    service = EvidenceResolutionService(client=FakeMediaClient(refs_without_thumbnail={ref}))

    items = service.resolve_refs([ref])

    assert items[0].resolved is True
    assert items[0].content_url is not None
    assert items[0].thumbnail_url is None
    assert items[0].thumbnail_available is False
    assert items[0].thumbnail_status == "unsupported"


def test_evidence_resolution_fallback_when_media_service_is_unavailable() -> None:
    ref = "s3://vigilante-frames/frames/cam01/frame.jpg"
    service = EvidenceResolutionService(
        client=FakeMediaClient(failures={ref: MediaClientError("media_service_unavailable", "down")})
    )

    items = service.resolve_refs([ref])

    assert items[0].resolved is False
    assert items[0].ref == ref
    assert items[0].error == "media_service_unavailable"


def test_evidence_resolution_fallback_when_reference_does_not_exist() -> None:
    ref = "s3://vigilante-frames/frames/missing.jpg"
    service = EvidenceResolutionService(
        client=FakeMediaClient(failures={ref: MediaClientError("remote_object_not_found", "missing", status_code=404)})
    )

    items = service.resolve_refs([ref])

    assert items[0].resolved is False
    assert items[0].ref == ref
    assert items[0].error == "remote_object_not_found"


def test_operational_endpoints_are_enriched_with_resolved_media(auth_headers) -> None:
    fake_client = FakeMediaClient()
    app.dependency_overrides[evidence_resolution_service_dependency] = lambda: EvidenceResolutionService(
        client=fake_client
    )
    try:
        with get_session() as session:
            ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
            ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
            suggestion = list_case_suggestions(session, limit=50)[0]
            resolve_case_suggestion(
                session,
                suggestion.suggestion_id,
                CaseSuggestionResolutionRequest.model_validate(
                    load_json_fixture("tests/fixtures/case_suggestion_resolution_accepted.json")
                ),
            )
            case = promote_case_suggestion(
                session,
                suggestion.suggestion_id,
                PromoteCaseSuggestionRequest.model_validate(
                    load_json_fixture("tests/fixtures/case_suggestion_promote.json")
                ),
            )

        client = TestClient(app)
        client.headers.update(auth_headers("maria"))

        case_detail = client.get(f"/api/v1/cases/{case.case_id}").json()
        review = client.get("/api/v1/manual-reviews").json()[0]
        suggestion_detail = client.get(f"/api/v1/case-suggestions/{suggestion.suggestion_id}").json()
        timeline_item = client.get("/api/v1/timeline/evt_rec_case_suggestion_created_001").json()

        assert case_detail["evidence_media"][0]["resolved"] is True
        assert case_detail["evidence_media"][0]["content_url"].startswith("http://media.local/api/v1/media/")
        assert case_detail["evidence_media"][0]["thumbnail_url"].startswith("http://media.local/api/v1/media/")
        assert review["payload"]["evidence_refs"] == ["tests/fixtures/images/face_manual_review.jpg"]
        assert review["evidence_media"][0]["resolved"] is True
        assert suggestion_detail["payload"]["evidence_refs"] == ["tests/fixtures/images/face_low_quality.jpg"]
        assert suggestion_detail["evidence_media"][0]["resolved"] is True
        assert timeline_item["evidence_media"][0]["ref"] == "tests/fixtures/images/face_low_quality.jpg"
        assert fake_client.calls
    finally:
        app.dependency_overrides.pop(evidence_resolution_service_dependency, None)


def test_endpoint_enrichment_preserves_resource_when_reference_is_unresolved(auth_headers) -> None:
    ref = "tests/fixtures/images/face_manual_review.jpg"
    fake_client = FakeMediaClient(failures={ref: MediaClientError("remote_object_not_found", "missing", status_code=404)})
    app.dependency_overrides[evidence_resolution_service_dependency] = lambda: EvidenceResolutionService(
        client=fake_client
    )
    try:
        with get_session() as session:
            ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))

        client = TestClient(app)
        client.headers.update(auth_headers())

        response = client.get("/api/v1/manual-reviews")

        assert response.status_code == 200
        item = response.json()[0]
        assert item["payload"]["evidence_refs"] == [ref]
        assert item["evidence_media"][0]["resolved"] is False
        assert item["evidence_media"][0]["error"] == "remote_object_not_found"
    finally:
        app.dependency_overrides.pop(evidence_resolution_service_dependency, None)
