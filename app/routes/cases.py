from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.case_record_service import CaseRecordRead, get_case, list_cases
from app.services.workflow_exceptions import WorkflowNotFoundError

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


@router.get("", response_model=list[CaseRecordRead])
def get_case_list(
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[CaseRecordRead]:
    return list_cases(session, limit=limit)


@router.get("/{case_id}", response_model=CaseRecordRead)
def get_case_item(case_id: str, session: Session = Depends(session_dependency)) -> CaseRecordRead:
    try:
        return get_case(session, case_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
