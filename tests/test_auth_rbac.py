from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.main import app
from app.services.case_suggestion_service import (
    CaseSuggestionResolutionRequest,
    list_case_suggestions,
    resolve_case_suggestion,
)
from app.services.events import ingest_event, list_timeline
from app.services.manual_review_service import list_manual_reviews

ANALYST_USER_ID = "00000000-0000-0000-0000-000000000101"


def _login(client: TestClient, username: str, password: str = "demo123") -> dict:
    return client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()


def test_login_invalid_login_me_and_logout_flow():
    client = TestClient(app)

    login = client.post("/api/v1/auth/login", json={"username": "julio", "password": "demo123"})
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "julio"
    assert body["user"]["role"] == "analyst"
    assert body["scope"]["organization_ids"] == ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"]

    invalid = client.post("/api/v1/auth/login", json={"username": "julio", "password": "wrong"})
    assert invalid.status_code == 401

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user_id"] == ANALYST_USER_ID
    assert me.json()["site_ids"] == ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"]

    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert "stateless" in logout.json()["message"].lower()


def test_sensitive_endpoint_denies_missing_auth():
    client = TestClient(app)

    response = client.get("/api/v1/cases")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_read_endpoint_allows_scoped_analyst(auth_headers):
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))

    client = TestClient(app)
    response = client.get("/api/v1/manual-reviews", headers=auth_headers("julio"))

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_write_endpoint_denies_wrong_role(auth_headers):
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        review = list_manual_reviews(session, limit=50)[0]

    client = TestClient(app)
    response = client.post(
        f"/api/v1/manual-reviews/{review.review_id}/resolve",
        headers=auth_headers("auditor"),
        json={"decision": "approved", "decision_reason": "read only user cannot resolve", "resolved_by": "auditor"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Analyst role is required"


def test_scope_denies_resource_outside_user_organization(auth_headers):
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        suggestion = list_case_suggestions(session, limit=50)[0]

    client = TestClient(app)
    response = client.get(f"/api/v1/case-suggestions/{suggestion.suggestion_id}", headers=auth_headers("ana"))

    assert response.status_code == 403
    assert response.json()["detail"] == "Resource is outside the authenticated user's scope"


def test_analyst_cannot_promote_case_suggestion(auth_headers):
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        suggestion = list_case_suggestions(session, limit=50)[0]
        resolve_case_suggestion(
            session,
            suggestion.suggestion_id,
            CaseSuggestionResolutionRequest(
                decision="accepted",
                decision_reason="prepared for promote authorization test",
                resolved_by="fixture",
            ),
        )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/case-suggestions/{suggestion.suggestion_id}/promote",
        headers=auth_headers("julio"),
        json={
            "resolved_by": "julio",
            "case_type": "unresolved_subject_case",
            "title": "Role denied promote",
            "priority": "medium",
            "severity": "medium",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Supervisor role is required"


def test_action_audit_uses_authenticated_user_not_payload(auth_headers):
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_manual_review_required.json"))
        review = list_manual_reviews(session, limit=50)[0]

    client = TestClient(app)
    response = client.post(
        f"/api/v1/manual-reviews/{review.review_id}/resolve",
        headers=auth_headers("julio"),
        json={"decision": "approved", "decision_reason": "confirmed by authenticated analyst", "resolved_by": "mallory"},
    )

    assert response.status_code == 200
    assert response.json()["resolved_by"] == "julio"

    with get_session() as session:
        audit_events = [item for item in list_timeline(session, limit=50) if item.event_type == "manual_review_resolved"]

    assert len(audit_events) == 1
    audit_payload = audit_events[0].payload["manual_review_resolution"]
    assert audit_payload["resolved_by"] == "julio"
    assert audit_payload["resolved_by_user_id"] == ANALYST_USER_ID


def test_supervisor_can_promote_with_real_authenticated_actor(auth_headers):
    with get_session() as session:
        ingest_event(session, load_fixture_event("tests/fixtures/recognition_case_suggestion_created.json"))
        suggestion = list_case_suggestions(session, limit=50)[0]
        resolve_case_suggestion(
            session,
            suggestion.suggestion_id,
            CaseSuggestionResolutionRequest(
                decision="accepted",
                decision_reason="prepared for supervisor promote",
                resolved_by="fixture",
            ),
        )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/case-suggestions/{suggestion.suggestion_id}/promote",
        headers=auth_headers("maria"),
        json={
            "resolved_by": "spoofed",
            "case_type": "unresolved_subject_case",
            "title": "Supervisor promoted case",
            "priority": "medium",
            "severity": "medium",
        },
    )

    assert response.status_code == 200
    assert response.json()["case_payload"]["resolved_by"] == "maria"
