from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.case_assignment_service import CaseAssignRequest, CaseUnassignRequest, assign_case, unassign_case
from app.services.case_lifecycle_service import CaseStatusChangeRequest, change_case_status
from app.services.case_note_service import CaseNoteCreateRequest, add_case_note
from app.services.case_query_service import get_case_detail, list_cases_filtered
from app.services.case_record_service import CaseRecordRead, PromoteCaseSuggestionRequest
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    list_case_suggestions,
    promote_case_suggestion,
    resolve_case_suggestion,
)
from app.services.events import RecognitionEventEnvelope, ingest_event, list_timeline
from app.services.manual_review_service import list_manual_reviews


def load_json_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_case_suggestion_event(index: int) -> RecognitionEventEnvelope:
    payload = load_json_fixture("tests/fixtures/recognition_case_suggestion_created.json")
    camera_tail = f"{111111111000 + index:012d}"
    track_tail = f"{222222222000 + index:012d}"
    subject_tail = f"{333333333000 + index:012d}"
    payload["event_id"] = f"evt_rec_case_suggestion_created_{index:03d}"
    payload["payload"]["evidence_refs"] = [f"tests/fixtures/images/face_low_quality_{index}.jpg"]
    payload["payload"]["current_subject_id"] = f"33333333-3333-3333-3333-{subject_tail}"
    payload["payload"]["current_track_id"] = f"22222222-2222-2222-2222-{track_tail}"
    payload["payload"]["target_subject_id"] = "33333333-3333-3333-3333-333333333399"
    payload["context"]["camera_id"] = f"11111111-1111-1111-1111-{camera_tail}"
    payload["context"]["track_id"] = payload["payload"]["current_track_id"]
    payload["context"]["subject_id"] = payload["payload"]["current_subject_id"]
    payload["context"]["idempotency_key"] = f"recognition:{payload['event_id']}"
    return RecognitionEventEnvelope.model_validate(payload)


def create_case_from_event(
    session,
    *,
    index: int,
    title: str,
    priority: int | str = "medium",
    severity: str = "medium",
) -> CaseRecordRead:
    ingest_event(session, build_case_suggestion_event(index))
    suggestion = list_case_suggestions(session, limit=50, status="pending")[0]
    resolve_case_suggestion(
        session,
        suggestion.suggestion_id,
        CaseSuggestionResolutionRequest.model_validate(
            load_json_fixture("tests/fixtures/case_suggestion_resolution_accepted.json")
        ),
    )
    promote_payload = load_json_fixture("tests/fixtures/case_suggestion_promote.json")
    promote_payload.update({"title": title, "priority": priority, "severity": severity})
    return promote_case_suggestion(
        session,
        suggestion.suggestion_id,
        PromoteCaseSuggestionRequest.model_validate(promote_payload),
    )


def test_case_assignment_reassignment_unassignment_and_idempotency():
    with get_session() as session:
        case = create_case_from_event(session, index=11, title="Assignment lifecycle case")

        assign_request = CaseAssignRequest.model_validate(load_json_fixture("tests/fixtures/case_assign_julio.json"))
        assigned = assign_case(session, case.case_id, assign_request)
        assigned_again = assign_case(session, case.case_id, assign_request)

        reassign_request = CaseAssignRequest.model_validate(load_json_fixture("tests/fixtures/case_reassign_maria.json"))
        reassigned = assign_case(session, case.case_id, reassign_request)
        reassigned_again = assign_case(session, case.case_id, reassign_request)

        unassign_request = CaseUnassignRequest.model_validate(load_json_fixture("tests/fixtures/case_unassign.json"))
        unassigned = unassign_case(session, case.case_id, unassign_request)
        unassigned_again = unassign_case(session, case.case_id, unassign_request)
        timeline = list_timeline(session, limit=50)

    assert assigned.assigned_to == "julio"
    assert assigned.assigned_by == "julio"
    assert assigned.assignment_reason == "analyst taking ownership"
    assert assigned_again.assigned_to == "julio"
    assert reassigned.assigned_to == "maria"
    assert reassigned_again.assigned_to == "maria"
    assert unassigned.assigned_to is None
    assert unassigned_again.assigned_to is None

    event_types = [item.event_type for item in timeline]
    assert event_types.count("case_assigned") == 1
    assert event_types.count("case_reassigned") == 1
    assert event_types.count("case_unassigned") == 1


def test_case_filters_review_filters_suggestion_filters_and_dashboard_summary():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        first_case = create_case_from_event(session, index=21, title="Filtered Julio case", priority="medium")
        second_case = create_case_from_event(session, index=22, title="Filtered High case", priority=2, severity="high")
        ingest_event(session, build_case_suggestion_event(23))

        assign_case(
            session,
            first_case.case_id,
            CaseAssignRequest.model_validate(load_json_fixture("tests/fixtures/case_assign_julio.json")),
        )
        change_case_status(
            session,
            second_case.case_id,
            CaseStatusChangeRequest.model_validate(load_json_fixture("tests/fixtures/case_status_change_in_review.json")),
        )

        assigned_cases = list_cases_filtered(session, limit=50, assigned_to="julio")
        in_review_cases = list_cases_filtered(session, limit=50, status="in_review")
        high_priority_cases = list_cases_filtered(session, limit=50, priority=2)
        high_severity_cases = list_cases_filtered(session, limit=50, severity="high")
        search_cases = list_cases_filtered(session, limit=50, q="High")
        paged_cases = list_cases_filtered(session, limit=1, offset=1, sort_by="opened_at")
        pending_reviews = list_manual_reviews(session, limit=50, status="pending", priority=3)
        pending_suggestions = list_case_suggestions(session, limit=50, status="pending")

    assert [item.case_id for item in assigned_cases] == [first_case.case_id]
    assert [item.case_id for item in in_review_cases] == [second_case.case_id]
    assert [item.case_id for item in high_priority_cases] == [second_case.case_id]
    assert [item.case_id for item in high_severity_cases] == [second_case.case_id]
    assert [item.case_id for item in search_cases] == [second_case.case_id]
    assert len(paged_cases) == 1
    assert len(pending_reviews) == 1
    assert len(pending_suggestions) == 1

    client = TestClient(app)
    assert len(client.get("/api/v1/cases", params={"assigned_to": "julio"}).json()) == 1
    assert len(client.get("/api/v1/cases", params={"status": "in_review"}).json()) == 1
    assert len(client.get("/api/v1/cases", params={"limit": 1, "offset": 1, "sort_by": "opened_at"}).json()) == 1
    assert len(client.get("/api/v1/manual-reviews", params={"status": "pending", "priority": 3}).json()) == 1
    assert len(client.get("/api/v1/case-suggestions", params={"status": "pending", "limit": 1}).json()) == 1

    summary = client.get("/api/v1/dashboard/summary", params={"assigned_to": "julio"})
    assert summary.status_code == 200
    body = summary.json()
    assert body["total_cases"] == 2
    assert body["under_review_cases"] == 1
    assert body["cases_assigned_to_user"] == 1
    assert body["pending_manual_reviews"] == 1
    assert body["pending_case_suggestions"] == 1


def test_case_detail_is_enriched_for_web_consumption():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        case = create_case_from_event(session, index=31, title="Enriched detail case")
        assign_case(
            session,
            case.case_id,
            CaseAssignRequest.model_validate(load_json_fixture("tests/fixtures/case_assign_julio.json")),
        )
        add_case_note(
            session,
            case.case_id,
            CaseNoteCreateRequest.model_validate(load_json_fixture("tests/fixtures/case_note_add.json")),
        )
        detail = get_case_detail(session, case.case_id, recent_limit=10)

    assert detail.case_id == case.case_id
    assert detail.assigned_to == "julio"
    assert detail.notes
    assert detail.reviews
    assert detail.suggestions
    assert "case_assigned" in {item.event_type for item in detail.timeline}

    client = TestClient(app)
    response = client.get(f"/api/v1/cases/{case.case_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["assigned_to"] == "julio"
    assert payload["notes"]
    assert payload["reviews"]
    assert payload["suggestions"]
    assert "case_assigned" in {item["event_type"] for item in payload["timeline"]}
