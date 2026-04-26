from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import session_dependency
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.dashboard_service import DashboardSummaryRead, get_dashboard_summary
from app.services.rbac_service import require_sensitive_read

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryRead)
def get_dashboard_summary_item(
    assigned_to: str | None = None,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> DashboardSummaryRead:
    require_sensitive_read(current_user)
    return get_dashboard_summary(session, assigned_to=assigned_to, current_user=current_user)
