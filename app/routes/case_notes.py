from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import session_dependency
from app.services.case_note_service import CaseNoteCreateRequest, CaseNoteRead, add_case_note, list_case_notes
from app.services.workflow_exceptions import WorkflowNotFoundError, WorkflowValidationError

router = APIRouter(prefix="/api/v1/cases", tags=["case-notes"])


@router.get("/{case_id}/notes", response_model=list[CaseNoteRead])
def get_case_notes(case_id: str, session: Session = Depends(session_dependency)) -> list[CaseNoteRead]:
    try:
        return list_case_notes(session, case_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{case_id}/notes", response_model=CaseNoteRead, status_code=status.HTTP_200_OK)
def create_case_note(
    case_id: str,
    request: CaseNoteCreateRequest,
    session: Session = Depends(session_dependency),
) -> CaseNoteRead:
    try:
        return add_case_note(session, case_id, request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
