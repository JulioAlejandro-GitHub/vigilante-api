from __future__ import annotations

import json
from pathlib import Path

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.services.case_record_service import PromoteCaseSuggestionRequest, list_cases
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    get_case_suggestion,
    list_case_suggestions,
    promote_case_suggestion,
    resolve_case_suggestion,
)
from app.services.events import ingest_event, list_timeline
from app.services.manual_review_service import (
    ManualReviewResolutionRequest,
    get_manual_review,
    list_manual_reviews,
    resolve_manual_review,
)


def load_json_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_manual_review_resolution_is_audited_and_idempotent():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        review = get_manual_review(session, "cf4aada6-ccf0-5f31-aa12-9786e2fc5217") or list_manual_reviews(session, limit=50)[0]

        request = ManualReviewResolutionRequest.model_validate(
            load_json_fixture("tests/fixtures/manual_review_resolution_approved.json")
        )
        resolved = resolve_manual_review(session, review.review_id, request)
        resolved_again = resolve_manual_review(session, review.review_id, request)
        timeline = list_timeline(session, limit=50)

    assert resolved.status == "approved"
    assert resolved.decision_reason == "confirmed by analyst"
    assert resolved.resolved_by == "julio"
    assert resolved_again.resolution_event_id == resolved.resolution_event_id
    assert sum(1 for item in timeline if item.event_type == "manual_review_resolved") == 1


def test_identity_conflict_resolution_is_recorded_operationally():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_identity_conflict.json"))
        review = list_manual_reviews(session, limit=50)[0]
        request = ManualReviewResolutionRequest.model_validate(
            load_json_fixture("tests/fixtures/identity_conflict_resolution_confirm_identity.json")
        )
        resolved = resolve_manual_review(session, review.review_id, request)
        timeline = list_timeline(session, limit=50)

    assert resolved.review_type == "identity_conflict"
    assert resolved.status == "approved"
    assert resolved.resolution_payload["identity_resolution"] == "confirm_identity"
    assert resolved.resolution_payload["confirmed_person_profile_id"] == "44444444-4444-4444-4444-444444444441"
    assert sum(1 for item in timeline if item.event_type == "identity_conflict_resolved") == 1


def test_case_suggestion_can_be_deferred_then_accepted():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        suggestion = get_case_suggestion(session, list_case_suggestions(session, limit=50)[0].suggestion_id)
        deferred = resolve_case_suggestion(
            session,
            suggestion.suggestion_id,
            CaseSuggestionResolutionRequest.model_validate(
                load_json_fixture("tests/fixtures/case_suggestion_resolution_deferred.json")
            ),
        )
        accepted = resolve_case_suggestion(
            session,
            suggestion.suggestion_id,
            CaseSuggestionResolutionRequest.model_validate(
                load_json_fixture("tests/fixtures/case_suggestion_resolution_accepted.json")
            ),
        )

    assert deferred.status == "deferred"
    assert accepted.status == "accepted"
    assert accepted.decision_reason == "sufficient evidence for case creation"


def test_case_suggestion_can_be_rejected():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        suggestion = list_case_suggestions(session, limit=50)[0]
        rejected = resolve_case_suggestion(
            session,
            suggestion.suggestion_id,
            CaseSuggestionResolutionRequest.model_validate(
                load_json_fixture("tests/fixtures/case_suggestion_resolution_rejected.json")
            ),
        )

    assert rejected.status == "rejected"
    assert rejected.decision == "rejected"


def test_promote_case_suggestion_creates_case_once_and_writes_timeline_audit():
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        suggestion = list_case_suggestions(session, limit=50)[0]
        resolve_case_suggestion(
            session,
            suggestion.suggestion_id,
            CaseSuggestionResolutionRequest.model_validate(
                load_json_fixture("tests/fixtures/case_suggestion_resolution_accepted.json")
            ),
        )
        promote_request = PromoteCaseSuggestionRequest.model_validate(
            load_json_fixture("tests/fixtures/case_suggestion_promote.json")
        )
        case_first = promote_case_suggestion(session, suggestion.suggestion_id, promote_request)
        case_second = promote_case_suggestion(session, suggestion.suggestion_id, promote_request)
        cases = list_cases(session, limit=50)
        suggestion_after = get_case_suggestion(session, suggestion.suggestion_id)
        timeline = list_timeline(session, limit=50)

    assert case_first.case_id == case_second.case_id
    assert len(cases) == 1
    assert suggestion_after.promoted_case_id == case_first.case_id
    assert suggestion_after.status == "accepted"
    assert sum(1 for item in timeline if item.event_type == "case_record_created") == 1
