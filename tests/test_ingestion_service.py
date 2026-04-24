from __future__ import annotations

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.services.events import (
    get_timeline_by_source_event_id,
    ingest_event,
    list_case_suggestions,
    list_manual_reviews,
    list_timeline,
)


def test_ingest_manual_review_fixture_materializes_timeline_and_review_projection():
    with get_session() as session:
        result = ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        timeline = list_timeline(session, limit=50)
        reviews = list_manual_reviews(session, limit=50)

    assert result.status == "applied"
    assert len(timeline) == 1
    assert timeline[0].source_event_id == "evt_rec_manual_review_required_001"
    assert timeline[0].event_type == "manual_review_required"
    assert timeline[0].track_id == "22222222-2222-2222-2222-222222222244"
    assert timeline[0].payload["review_type"] == "cross_camera_correlation"

    assert len(reviews) == 1
    assert reviews[0].review_type == "cross_camera_correlation"
    assert reviews[0].status == "pending"
    assert reviews[0].camera_id == "11111111-1111-1111-1111-111111111104"
    assert reviews[0].payload["cross_camera_assessment"]["evaluated_candidates"] == 2


def test_same_event_id_is_idempotent_for_manual_review_and_timeline():
    with get_session() as session:
        first = ingest_event(session, load_fixture_event("tests/fixtures/recognition_identity_conflict.json"))
        second = ingest_event(session, load_fixture_event("tests/fixtures/recognition_identity_conflict.json"))
        timeline = list_timeline(session, limit=50)
        reviews = list_manual_reviews(session, limit=50)

    assert first.status == "applied"
    assert second.status == "duplicate"
    assert len(timeline) == 1
    assert len(reviews) == 1
    assert reviews[0].review_type == "identity_conflict"
    assert timeline[0].source_event_id == "evt_rec_identity_conflict_001"


def test_same_event_id_is_idempotent_for_case_suggestion_and_timeline():
    with get_session() as session:
        first = ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        second = ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        timeline = list_timeline(session, limit=50)
        suggestions = list_case_suggestions(session, limit=50)

    assert first.status == "applied"
    assert second.status == "duplicate"
    assert len(timeline) == 1
    assert len(suggestions) == 1
    assert suggestions[0].suggestion_type == "unresolved_subject_case"
    assert suggestions[0].evidence_count == 3


def test_timeline_preserves_original_payload_fields_for_auditability():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_face_detected_identified.json"))
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_recurrent_unresolved_subject.json"))
        identified = get_timeline_by_source_event_id(session, "evt_rec_face_detected_identified_001")
        recurrent = get_timeline_by_source_event_id(session, "evt_rec_recurrent_unresolved_subject_001")

    assert identified is not None
    assert recurrent is not None
    assert identified.payload["face_detection"]["usable"] is True
    assert identified.payload["match_confidence"] == 0.97
    assert identified.payload["semantic_descriptor"]["descriptor_backend"] == "simple_color_signature_v1"
    assert identified.payload["generation_trace"]["pipeline"] == "recognition.worker.slice1"

    assert recurrent.payload["recurrent_subject_assessment"]["evaluated_candidates"] == 2
    assert recurrent.payload["semantic_descriptor"]["appearance"]["dominant_palette"] == ["gray", "blue", "black"]
    assert recurrent.payload["generation_trace"]["step"] == "recurrent_subject_resolution"
