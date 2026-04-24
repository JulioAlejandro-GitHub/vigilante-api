from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_dependency
from app.services.events import TimelineEventRead, get_timeline_by_source_event_id, list_timeline

router = APIRouter(prefix="/api/v1/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEventRead])
def get_timeline(
    event_type: str | None = None,
    camera_id: str | None = None,
    subject_id: str | None = None,
    organization_id: str | None = None,
    site_id: str | None = None,
    limit: int = Query(default=get_settings().default_query_limit, ge=1),
    session: Session = Depends(session_dependency),
) -> list[TimelineEventRead]:
    return list_timeline(
        session,
        limit=limit,
        event_type=event_type,
        camera_id=camera_id,
        subject_id=subject_id,
        organization_id=organization_id,
        site_id=site_id,
    )


@router.get("/{source_event_id}", response_model=TimelineEventRead)
def get_timeline_item(source_event_id: str, session: Session = Depends(session_dependency)) -> TimelineEventRead:
    item = get_timeline_by_source_event_id(session, source_event_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    return item
