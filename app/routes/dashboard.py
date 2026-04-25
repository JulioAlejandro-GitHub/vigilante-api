from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import session_dependency
from app.services.dashboard_service import DashboardSummaryRead, get_dashboard_summary

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryRead)
def get_dashboard_summary_item(
    assigned_to: str | None = None,
    session: Session = Depends(session_dependency),
) -> DashboardSummaryRead:
    return get_dashboard_summary(session, assigned_to=assigned_to)
