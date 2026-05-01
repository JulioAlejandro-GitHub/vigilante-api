from __future__ import annotations

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TimelineEvent
from app.services.evidence_ref_classifier import evidence_ref_profile


T = TypeVar("T")


def live_first_rank(item: object) -> int:
    profile = evidence_ref_profile(item, max_refs=get_settings().media_resolution_max_refs)
    if profile.has_live:
        return 0
    if profile.is_fixture_only:
        return 2
    return 1


def apply_live_first_order(items: list[T]) -> list[T]:
    if not any(live_first_rank(item) == 0 for item in items):
        return items
    return sorted(items, key=live_first_rank)


def remove_fixture_only_items_when_live(items: list[T], *, live_evidence_exists: bool) -> list[T]:
    if not live_evidence_exists:
        return items
    return [item for item in items if live_first_rank(item) != 2]


def timeline_has_live_evidence(session: Session) -> bool:
    settings = get_settings()
    limit = max(settings.max_query_limit, settings.live_projection_max_events)
    rows = session.scalars(select(TimelineEvent).order_by(TimelineEvent.occurred_at.desc()).limit(limit)).all()
    return any(evidence_ref_profile(row.payload, max_refs=settings.media_resolution_max_refs).has_live for row in rows)
