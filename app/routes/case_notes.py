from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import session_dependency
from app.services.case_record_service import get_case
from app.services.case_note_service import CaseNoteCreateRequest, CaseNoteRead, add_case_note, list_case_notes
from app.services.current_user_service import CurrentUser, get_current_user
from app.services.rbac_service import require_analyst, require_sensitive_read
from app.services.scope_service import require_item_scope
from app.services.workflow_exceptions import WorkflowNotFoundError, WorkflowValidationError

router = APIRouter(prefix="/api/v1/cases", tags=["case-notes"])


@router.get("/{case_id}/notes", response_model=list[CaseNoteRead])
def get_case_notes(
    case_id: str,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CaseNoteRead]:
    try:
        require_sensitive_read(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case)
        return list_case_notes(session, case_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{case_id}/notes", response_model=CaseNoteRead, status_code=status.HTTP_200_OK)
def create_case_note(
    case_id: str,
    request: CaseNoteCreateRequest,
    session: Session = Depends(session_dependency),
    current_user: CurrentUser = Depends(get_current_user),
) -> CaseNoteRead:
    try:
        require_analyst(current_user)
        case = get_case(session, case_id)
        require_item_scope(current_user, case, operate=True)
        auth_request = request.model_copy(
            update={
                "author": current_user.username,
                "author_user_id": current_user.user_id,
            }
        )
        return add_case_note(session, case_id, auth_request)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
