from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.events import ingest_event, list_case_suggestions, list_manual_reviews


def test_health_and_projection_endpoints():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))

    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    timeline = client.get("/api/v1/timeline")
    assert timeline.status_code == 200
    timeline_items = timeline.json()
    assert len(timeline_items) == 2

    timeline_item = client.get("/api/v1/timeline/evt_rec_manual_review_required_001")
    assert timeline_item.status_code == 200
    assert timeline_item.json()["event_type"] == "manual_review_required"

    reviews = client.get("/api/v1/manual-reviews")
    assert reviews.status_code == 200
    review_items = reviews.json()
    assert len(review_items) == 1
    review_id = review_items[0]["review_id"]

    review_item = client.get(f"/api/v1/manual-reviews/{review_id}")
    assert review_item.status_code == 200
    assert review_item.json()["review_type"] == "cross_camera_correlation"

    suggestions = client.get("/api/v1/case-suggestions")
    assert suggestions.status_code == 200
    suggestion_items = suggestions.json()
    assert len(suggestion_items) == 1
    suggestion_id = suggestion_items[0]["suggestion_id"]

    suggestion_item = client.get(f"/api/v1/case-suggestions/{suggestion_id}")
    assert suggestion_item.status_code == 200
    assert suggestion_item.json()["suggestion_type"] == "unresolved_subject_case"


def test_projection_endpoints_do_not_duplicate_when_related_events_collapse_to_same_review_id():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_identity_conflict.json"))
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required_identity_conflict.json"))
        reviews = list_manual_reviews(session, limit=50)
        suggestions = list_case_suggestions(session, limit=50)

    client = TestClient(app)

    response = client.get("/api/v1/manual-reviews")
    assert response.status_code == 200
    review_items = response.json()
    assert len(reviews) == 1
    assert len(review_items) == 1
    assert review_items[0]["review_type"] == "identity_conflict"

    suggestion_response = client.get("/api/v1/case-suggestions")
    assert suggestion_response.status_code == 200
    assert suggestion_response.json() == suggestions
