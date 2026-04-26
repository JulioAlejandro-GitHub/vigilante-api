from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CaseRecord
from app.services.case_record_service import case_lifecycle_status, read_case_assignment
from app.services.case_suggestion_service import list_case_suggestions
from app.services.manual_review_service import list_manual_reviews
from app.services.scope_service import filter_items_by_scope, scope_allows


class DashboardSummaryRead(BaseModel):
    total_cases: int
    open_cases: int
    under_review_cases: int
    unassigned_cases: int
    assigned_cases: int
    cases_assigned_to_user: int | None = None
    pending_manual_reviews: int
    pending_case_suggestions: int


def get_dashboard_summary(session: Session, *, assigned_to: str | None = None, current_user=None) -> DashboardSummaryRead:
    rows = list(session.scalars(select(CaseRecord)).all())
    if current_user is not None:
        rows = [
            row
            for row in rows
            if scope_allows(
                current_user,
                organization_id=str(row.organization_id) if row.organization_id else (row.case_metadata or {}).get("raw_organization_id"),
                site_id=str(row.site_id) if row.site_id else (row.case_metadata or {}).get("raw_site_id"),
            )
        ]
    assignments = [read_case_assignment(row) for row in rows]
    pending_manual_reviews = list_manual_reviews(session, limit=get_settings().max_query_limit, status="pending")
    pending_case_suggestions = list_case_suggestions(session, limit=get_settings().max_query_limit, status="pending")
    if current_user is not None:
        pending_manual_reviews = filter_items_by_scope(current_user, pending_manual_reviews)
        pending_case_suggestions = filter_items_by_scope(current_user, pending_case_suggestions)
    return DashboardSummaryRead(
        total_cases=len(rows),
        open_cases=sum(1 for row in rows if case_lifecycle_status(row) in {"open", "reopened"}),
        under_review_cases=sum(1 for row in rows if case_lifecycle_status(row) in {"in_review", "under_review"}),
        unassigned_cases=sum(1 for item in assignments if item.get("assigned_to") is None),
        assigned_cases=sum(1 for item in assignments if item.get("assigned_to") is not None),
        cases_assigned_to_user=(
            sum(1 for item in assignments if item.get("assigned_to") == assigned_to) if assigned_to else None
        ),
        pending_manual_reviews=len(pending_manual_reviews),
        pending_case_suggestions=len(pending_case_suggestions),
    )
