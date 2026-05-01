from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.case_query_service import list_cases_filtered
from app.services.case_record_service import PromoteCaseSuggestionRequest, list_cases
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    list_case_suggestions,
    promote_case_suggestion,
    resolve_case_suggestion,
)
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.events import RecognitionEventEnvelope, ingest_event, list_timeline
from app.services.media_models import MediaAssetResponse


LIVE_REF = "s3://vigilante-frames/frames/cam01/live-frame.jpg"


def load_json_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def live_event_from_fixture(path: str, *, event_id: str, ref: str = LIVE_REF) -> RecognitionEventEnvelope:
    payload = load_json_fixture(path)
    payload["event_id"] = event_id
    payload["occurred_at"] = datetime.now(timezone.utc).isoformat()
    payload["emitted_at"] = payload["occurred_at"]
    payload["payload"]["evidence_refs"] = [ref]
    payload["payload"].setdefault("semantic_descriptor", {})["source_frame_ref"] = ref
    payload["context"]["idempotency_key"] = f"recognition:{event_id}"
    return RecognitionEventEnvelope.model_validate(payload)


def create_case_from_suggestion_event(session, event: RecognitionEventEnvelope):
    result = ingest_event(session, event)
    suggestion = result.case_suggestion
    assert suggestion is not None
    resolve_case_suggestion(
        session,
        suggestion.suggestion_id,
        CaseSuggestionResolutionRequest.model_validate(load_json_fixture("tests/fixtures/case_suggestion_resolution_accepted.json")),
    )
    return promote_case_suggestion(
        session,
        suggestion.suggestion_id,
        PromoteCaseSuggestionRequest.model_validate(load_json_fixture("tests/fixtures/case_suggestion_promote.json")),
    )


class FakeMediaClient:
    def resolve(self, ref: str) -> MediaAssetResponse:
        media_id = "media_live_frame"
        return MediaAssetResponse(
            media_id=media_id,
            media_type="frame",
            storage_backend="s3",
            bucket="vigilante-frames",
            object_key="frames/cam01/live-frame.jpg",
            content_type="image/jpeg",
            metadata={"width": 640, "height": 480},
            source_ref=ref,
            content_url=f"http://media.local/api/v1/media/{media_id}/content",
            thumbnail_url=f"http://media.local/api/v1/media/{media_id}/thumbnail",
            thumbnail_available=True,
            thumbnail_status="available",
            clip_available=False,
            clip_status="insufficient_frames",
            metadata_url=f"http://media.local/api/v1/media/{media_id}",
        )


def test_timeline_prioritizes_live_recent_evidence_over_fixture_refs() -> None:
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        ingest_event(
            session,
            live_event_from_fixture(
                "tests/fixtures/recognition_face_detected_identified.json",
                event_id="evt_live_face_detected_identified_001",
            ),
        )
        timeline = list_timeline(session, limit=50)

    assert timeline[0].source_event_id == "evt_live_face_detected_identified_001"
    assert timeline[0].payload["evidence_refs"] == [LIVE_REF]
    assert timeline[-1].payload["evidence_refs"][0].startswith("tests/fixtures/")


def test_case_suggestions_hide_fixture_only_items_when_live_suggestions_exist() -> None:
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        ingest_event(
            session,
            live_event_from_fixture(
                "tests/fixtures/recognition_case_suggestion_created.json",
                event_id="evt_live_case_suggestion_created_001",
            ),
        )
        suggestions = list_case_suggestions(session, limit=50)

    assert suggestions
    assert suggestions[0].source_event_id == "evt_live_case_suggestion_created_001"
    assert all(not item.payload["evidence_refs"][0].startswith("tests/fixtures/") for item in suggestions)


def test_cases_hide_fixture_only_records_when_live_cases_exist() -> None:
    with get_session() as session:
        create_case_from_suggestion_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        live_case = create_case_from_suggestion_event(
            session,
            live_event_from_fixture(
                "tests/fixtures/recognition_case_suggestion_created.json",
                event_id="evt_live_case_suggestion_for_case_001",
            )
        )
        cases = list_cases(session, limit=50)
        filtered = list_cases_filtered(session, limit=50)

    assert [item.case_id for item in cases] == [live_case.case_id]
    assert [item.case_id for item in filtered] == [live_case.case_id]
    assert cases[0].case_payload["source_case_suggestion_payload"]["evidence_refs"] == [LIVE_REF]


def test_evidence_media_resolves_live_refs_on_timeline_endpoint(auth_headers) -> None:
    app.dependency_overrides[evidence_resolution_service_dependency] = lambda: EvidenceResolutionService(
        client=FakeMediaClient()
    )
    try:
        with get_session() as session:
            ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
            ingest_event(
                session,
                live_event_from_fixture(
                    "tests/fixtures/recognition_face_detected_identified.json",
                    event_id="evt_live_face_media_001",
                ),
            )

        client = TestClient(app)
        client.headers.update(auth_headers())
        payload = client.get("/api/v1/timeline").json()

        assert payload[0]["source_event_id"] == "evt_live_face_media_001"
        assert payload[0]["payload"]["evidence_refs"] == [LIVE_REF]
        assert payload[0]["evidence_media"][0]["ref"] == LIVE_REF
        assert payload[0]["evidence_media"][0]["resolved"] is True
        assert payload[0]["evidence_media"][0]["content_url"].endswith("/content")
    finally:
        app.dependency_overrides.pop(evidence_resolution_service_dependency, None)


def test_fixture_fallback_remains_when_no_live_evidence_exists() -> None:
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        fixture_case = create_case_from_suggestion_event(
            session,
            load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"),
        )
        timeline = list_timeline(session, limit=50)
        suggestions = list_case_suggestions(session, limit=50)
        cases = list_cases(session, limit=50)

    assert timeline
    assert suggestions
    assert cases
    assert suggestions[0].payload["evidence_refs"][0].startswith("tests/fixtures/")
    assert cases[0].case_id == fixture_case.case_id
