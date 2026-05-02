from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5

from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.case_record_service import PromoteCaseSuggestionRequest, list_cases
from app.services.case_suggestion_service import CaseSuggestionResolutionRequest, list_case_suggestions, promote_case_suggestion, resolve_case_suggestion
from app.services.evidence_ref_classifier import evidence_ref_profile, is_fixture_evidence_ref, is_live_evidence_ref
from app.services.evidence_resolution_service import EvidenceResolutionService, evidence_resolution_service_dependency
from app.services.events import RecognitionEventEnvelope, ingest_event
from app.services.media_models import MediaAssetResponse


LIVE_REF = "s3://vigilante-frames/frames/cam01/live-case-suggestion.jpg"
LIVE_REF_2 = "s3://vigilante-frames/frames/cam01/live-case-suggestion-2.jpg"
LIVE_REF_3 = "s3://vigilante-frames/frames/cam01/live-case-suggestion-3.jpg"


def load_json_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def live_event_from_fixture(
    path: str,
    *,
    event_id: str,
    ref: str = LIVE_REF,
    occurred_at: datetime | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    track_id: str | None = None,
) -> RecognitionEventEnvelope:
    payload = load_json_fixture(path)
    event_ts = occurred_at or datetime.now(timezone.utc)
    payload["event_id"] = event_id
    payload["occurred_at"] = event_ts.isoformat()
    payload["emitted_at"] = event_ts.isoformat()
    payload["payload"]["evidence_refs"] = [ref]
    payload["payload"].setdefault("semantic_descriptor", {})["source_frame_ref"] = ref
    if camera_id:
        payload["context"]["camera_id"] = camera_id
    if subject_id:
        payload["context"]["subject_id"] = subject_id
    if track_id:
        payload["context"]["track_id"] = track_id
    payload["context"]["idempotency_key"] = f"recognition:{event_id}"
    return RecognitionEventEnvelope.model_validate(payload)


def deterministic_uuid(name: str) -> str:
    return str(uuid5(UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8"), name))


class FakeMediaClient:
    def resolve(self, ref: str) -> MediaAssetResponse:
        media_id = f"media_{abs(hash(ref))}"
        return MediaAssetResponse(
            media_id=media_id,
            media_type="frame",
            storage_backend="s3",
            bucket="vigilante-frames",
            object_key=ref.removeprefix("s3://vigilante-frames/"),
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


def create_fixture_case(session):
    result = ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
    assert result.case_suggestion is not None
    resolve_case_suggestion(
        session,
        result.case_suggestion.suggestion_id,
        CaseSuggestionResolutionRequest.model_validate(load_json_fixture("tests/fixtures/case_suggestion_resolution_accepted.json")),
    )
    return promote_case_suggestion(
        session,
        result.case_suggestion.suggestion_id,
        PromoteCaseSuggestionRequest.model_validate(load_json_fixture("tests/fixtures/case_suggestion_promote.json")),
    )


def test_live_vs_fixture_classification_for_suggestions_and_cases() -> None:
    assert is_fixture_evidence_ref("tests/fixtures/images/face_low_quality.jpg")
    assert is_live_evidence_ref(LIVE_REF)

    with get_session() as session:
        fixture_case = create_fixture_case(session)
        ingest_event(
            session,
            live_event_from_fixture(
                "tests/fixtures/recognition_manual_review_required.json",
                event_id="evt_live_manual_review_for_classification",
            ),
        )
        live_suggestion = list_case_suggestions(session, limit=50)[0]

    assert evidence_ref_profile(live_suggestion).has_live is True
    assert evidence_ref_profile(fixture_case).is_fixture_only is True


def test_projects_live_case_suggestion_from_manual_review_event() -> None:
    with get_session() as session:
        event = live_event_from_fixture(
            "tests/fixtures/recognition_manual_review_required.json",
            event_id="evt_live_manual_review_case_suggestion_001",
        )
        ingest_event(session, event)
        suggestions = list_case_suggestions(session, limit=50)

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.source_event_id.startswith("evt_api_live_case_suggestion_")
    assert suggestion.source_event_type == "case_suggestion_created"
    assert suggestion.suggestion_type == "manual_review"
    assert suggestion.payload["evidence_refs"] == [LIVE_REF]
    assert suggestion.payload["source_event_ids"] == ["evt_live_manual_review_case_suggestion_001"]
    assert suggestion.payload["trigger_source_event_type"] == "manual_review_required"


def test_projects_live_case_suggestion_from_camera_evidence_threshold() -> None:
    camera_id = deterministic_uuid("live-camera-threshold")
    base_ts = datetime.now(timezone.utc)

    with get_session() as session:
        for index, ref in enumerate([LIVE_REF, LIVE_REF_2, LIVE_REF_3], start=1):
            ingest_event(
                session,
                live_event_from_fixture(
                    "tests/fixtures/recognition_face_detected_unidentified.json",
                    event_id=f"evt_live_camera_threshold_{index:03d}",
                    ref=ref,
                    occurred_at=base_ts + timedelta(seconds=index),
                    camera_id=camera_id,
                    subject_id=deterministic_uuid(f"live-camera-threshold-subject-{index}"),
                    track_id=deterministic_uuid(f"live-camera-threshold-track-{index}"),
                ),
            )
        suggestions = list_case_suggestions(session, limit=50)

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.suggestion_type == "unresolved_subject_case"
    assert suggestion.payload["projection_group_type"] == "camera"
    assert suggestion.payload["projection_group_key"] == camera_id
    assert suggestion.payload["evidence_refs"] == [LIVE_REF_3, LIVE_REF_2, LIVE_REF]
    assert suggestion.evidence_count == 3


def test_derived_live_case_suggestion_resolves_evidence_media_on_endpoint(auth_headers) -> None:
    app.dependency_overrides[evidence_resolution_service_dependency] = lambda: EvidenceResolutionService(
        client=FakeMediaClient()
    )
    try:
        with get_session() as session:
            ingest_event(
                session,
                live_event_from_fixture(
                    "tests/fixtures/recognition_manual_review_required.json",
                    event_id="evt_live_manual_review_case_suggestion_media_001",
                ),
            )

        client = TestClient(app)
        client.headers.update(auth_headers())
        payload = client.get("/api/v1/case-suggestions").json()

        assert payload[0]["payload"]["evidence_refs"] == [LIVE_REF]
        assert payload[0]["evidence_media"][0]["ref"] == LIVE_REF
        assert payload[0]["evidence_media"][0]["resolved"] is True
        assert payload[0]["evidence_media"][0]["content_url"].endswith("/content")
    finally:
        app.dependency_overrides.pop(evidence_resolution_service_dependency, None)


def test_derived_live_case_suggestion_can_be_promoted_to_real_case() -> None:
    with get_session() as session:
        ingest_event(
            session,
            live_event_from_fixture(
                "tests/fixtures/recognition_recurrent_unresolved_subject.json",
                event_id="evt_live_recurrent_subject_for_case_001",
            ),
        )
        suggestion = list_case_suggestions(session, limit=50)[0]
        resolve_case_suggestion(
            session,
            suggestion.suggestion_id,
            CaseSuggestionResolutionRequest(
                decision="accepted",
                decision_reason="live evidence is sufficient for case creation",
                resolved_by="maria",
            ),
        )
        case = promote_case_suggestion(
            session,
            suggestion.suggestion_id,
            PromoteCaseSuggestionRequest(
                resolved_by="maria",
                case_type=suggestion.suggestion_type,
                title="Live unresolved subject case",
                priority="medium",
                severity=str(suggestion.payload.get("suggested_severity", "medium")),
            ),
        )
        cases = list_cases(session, limit=50)

    assert [item.case_id for item in cases] == [case.case_id]
    assert cases[0].source_suggestion_id == suggestion.suggestion_id
    assert cases[0].case_payload["source_case_suggestion_payload"]["evidence_refs"] == [LIVE_REF]


def test_cases_hide_fixture_records_when_live_flow_has_no_promoted_case_yet() -> None:
    with get_session() as session:
        create_fixture_case(session)
        ingest_event(
            session,
            live_event_from_fixture(
                "tests/fixtures/recognition_manual_review_required.json",
                event_id="evt_live_manual_review_without_case_001",
            ),
        )
        cases = list_cases(session, limit=50)

    assert cases == []
