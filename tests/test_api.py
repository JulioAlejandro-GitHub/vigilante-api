from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.case_suggestion_service import get_case_suggestion
from app.services.events import get_manual_review, ingest_event, list_case_suggestions, list_manual_reviews, list_timeline


def load_json_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
        reviews = [item.model_dump(mode="json") for item in list_manual_reviews(session, limit=50)]
        suggestions = [item.model_dump(mode="json") for item in list_case_suggestions(session, limit=50)]

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


def test_http_action_endpoints_minimum_flow():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_identity_conflict.json"))
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        manual_review_id = get_manual_review(session, "cf4aada6-ccf0-5f31-aa12-9786e2fc5217")
        if manual_review_id is None:
            manual_review_id = list_manual_reviews(session, limit=50)[0]
        suggestion = list_case_suggestions(session, limit=50)[0]
        identity_review = [item for item in list_manual_reviews(session, limit=50) if item.review_type == "identity_conflict"][0]

    client = TestClient(app)

    review_response = client.post(
        f"/api/v1/manual-reviews/{manual_review_id.review_id}/resolve",
        json=load_json_fixture("tests/fixtures/manual_review_resolution_approved.json"),
    )
    assert review_response.status_code == 200
    assert review_response.json()["status"] == "approved"

    identity_response = client.post(
        f"/api/v1/manual-reviews/{identity_review.review_id}/resolve",
        json=load_json_fixture("tests/fixtures/identity_conflict_resolution_confirm_identity.json"),
    )
    assert identity_response.status_code == 200
    assert identity_response.json()["status"] == "approved"
    assert identity_response.json()["resolution_payload"]["identity_resolution"] == "confirm_identity"

    accept_response = client.post(
        f"/api/v1/case-suggestions/{suggestion.suggestion_id}/resolve",
        json=load_json_fixture("tests/fixtures/case_suggestion_resolution_accepted.json"),
    )
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "accepted"

    promote_response = client.post(
        f"/api/v1/case-suggestions/{suggestion.suggestion_id}/promote",
        json=load_json_fixture("tests/fixtures/case_suggestion_promote.json"),
    )
    assert promote_response.status_code == 200
    case_id = promote_response.json()["case_id"]
    assert promote_response.json()["source_suggestion_id"] == suggestion.suggestion_id
    assert promote_response.json()["case_type"] == "multi_event_tracking"

    cases_response = client.get("/api/v1/cases")
    assert cases_response.status_code == 200
    assert len(cases_response.json()) == 1

    case_detail = client.get(f"/api/v1/cases/{case_id}")
    assert case_detail.status_code == 200
    assert case_detail.json()["case_id"] == case_id

    suggestion_detail = client.get(f"/api/v1/case-suggestions/{suggestion.suggestion_id}")
    assert suggestion_detail.status_code == 200
    assert suggestion_detail.json()["promoted_case_id"] == case_id

    timeline_events = client.get("/api/v1/timeline").json()
    event_types = {item["event_type"] for item in timeline_events}
    assert "manual_review_resolved" in event_types
    assert "identity_conflict_resolved" in event_types
    assert "case_suggestion_resolved" in event_types
    assert "case_record_created" in event_types
