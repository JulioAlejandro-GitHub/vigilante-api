from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.case_lifecycle_service import (
    CaseCloseRequest,
    CaseReopenRequest,
    CaseStatusChangeRequest,
    change_case_status,
    close_case,
    reopen_case,
)
from app.services.case_note_service import CaseNoteCreateRequest, add_case_note, list_case_notes
from app.services.case_relation_service import (
    list_case_related_reviews,
    list_case_related_suggestions,
    list_case_timeline,
)
from app.services.case_record_service import CaseRecordRead, PromoteCaseSuggestionRequest
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    list_case_suggestions,
    promote_case_suggestion,
    resolve_case_suggestion,
)
from app.services.events import ingest_event, list_timeline


def load_json_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def create_promoted_case_from_slice2(session) -> CaseRecordRead:
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
    return promote_case_suggestion(
        session,
        suggestion.suggestion_id,
        PromoteCaseSuggestionRequest.model_validate(load_json_fixture("tests/fixtures/case_suggestion_promote.json")),
    )


def test_case_lifecycle_notes_and_case_timeline_are_idempotent():
    with get_session() as session:
        case = create_promoted_case_from_slice2(session)

        status_request = CaseStatusChangeRequest.model_validate(
            load_json_fixture("tests/fixtures/case_status_change_in_review.json")
        )
        in_review = change_case_status(session, case.case_id, status_request)
        in_review_again = change_case_status(session, case.case_id, status_request)

        note_request = CaseNoteCreateRequest.model_validate(load_json_fixture("tests/fixtures/case_note_add.json"))
        note = add_case_note(session, case.case_id, note_request)
        note_again = add_case_note(session, case.case_id, note_request)

        close_request = CaseCloseRequest.model_validate(load_json_fixture("tests/fixtures/case_close.json"))
        closed = close_case(session, case.case_id, close_request)
        closed_again = close_case(session, case.case_id, close_request)

        reopen_request = CaseReopenRequest.model_validate(load_json_fixture("tests/fixtures/case_reopen.json"))
        reopened = reopen_case(session, case.case_id, reopen_request)
        reopened_again = reopen_case(session, case.case_id, reopen_request)

        notes = list_case_notes(session, case.case_id)
        timeline = list_timeline(session, limit=50)
        case_timeline = list_case_timeline(session, case.case_id, limit=50)
        related_reviews = list_case_related_reviews(session, case.case_id, limit=50)
        related_suggestions = list_case_related_suggestions(session, case.case_id, limit=50)

    assert in_review.status == "in_review"
    assert in_review.db_status == "under_review"
    assert in_review_again.status == in_review.status

    assert note.note_id == note_again.note_id
    assert note.author == "julio"
    assert len(notes) == 1

    assert closed.status == "closed"
    assert closed.closed_at is not None
    assert closed_again.status == closed.status

    assert reopened.status == "reopened"
    assert reopened.closed_at is None
    assert reopened_again.status == reopened.status

    event_types = [item.event_type for item in timeline]
    assert event_types.count("case_status_changed") == 1
    assert event_types.count("case_note_added") == 1
    assert event_types.count("case_closed") == 1
    assert event_types.count("case_reopened") == 1

    case_event_types = {item.event_type for item in case_timeline}
    assert {"case_record_created", "case_status_changed", "case_note_added", "case_closed", "case_reopened"}.issubset(
        case_event_types
    )
    assert "case_suggestion_created" in case_event_types
    assert "case_suggestion_resolved" in case_event_types
    assert related_suggestions
    assert related_suggestions[0].promoted_case_id == case.case_id
    assert related_reviews


def test_case_lifecycle_http_endpoints_minimum_flow(auth_headers):
    with get_session() as session:
        case = create_promoted_case_from_slice2(session)

    client = TestClient(app)
    client.headers.update(auth_headers("maria"))

    cases_response = client.get("/api/v1/cases")
    assert cases_response.status_code == 200
    assert len(cases_response.json()) == 1

    case_response = client.get(f"/api/v1/cases/{case.case_id}")
    assert case_response.status_code == 200
    assert case_response.json()["case_id"] == case.case_id

    status_response = client.post(
        f"/api/v1/cases/{case.case_id}/status",
        json=load_json_fixture("tests/fixtures/case_status_change_in_review.json"),
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "in_review"
    assert status_response.json()["db_status"] == "under_review"

    note_response = client.post(
        f"/api/v1/cases/{case.case_id}/notes",
        json=load_json_fixture("tests/fixtures/case_note_add.json"),
    )
    assert note_response.status_code == 200
    assert note_response.json()["author"] == "maria"

    notes_response = client.get(f"/api/v1/cases/{case.case_id}/notes")
    assert notes_response.status_code == 200
    assert len(notes_response.json()) == 1

    close_response = client.post(
        f"/api/v1/cases/{case.case_id}/close",
        json=load_json_fixture("tests/fixtures/case_close.json"),
    )
    assert close_response.status_code == 200
    assert close_response.json()["status"] == "closed"

    reopen_response = client.post(
        f"/api/v1/cases/{case.case_id}/reopen",
        json=load_json_fixture("tests/fixtures/case_reopen.json"),
    )
    assert reopen_response.status_code == 200
    assert reopen_response.json()["status"] == "reopened"

    timeline_response = client.get(f"/api/v1/cases/{case.case_id}/timeline")
    assert timeline_response.status_code == 200
    event_types = {item["event_type"] for item in timeline_response.json()}
    assert {"case_record_created", "case_status_changed", "case_note_added", "case_closed", "case_reopened"}.issubset(
        event_types
    )

    reviews_response = client.get(f"/api/v1/cases/{case.case_id}/reviews")
    assert reviews_response.status_code == 200
    assert reviews_response.json()

    suggestions_response = client.get(f"/api/v1/cases/{case.case_id}/suggestions")
    assert suggestions_response.status_code == 200
    assert suggestions_response.json()[0]["promoted_case_id"] == case.case_id

    health_response = client.get("/health")
    assert health_response.status_code == 200
