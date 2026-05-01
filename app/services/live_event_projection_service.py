from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Camera, Site
from app.services.evidence_ref_classifier import evidence_ref_profile
from app.services.events import RecognitionEventEnvelope, SUPPORTED_EVENT_TYPES, as_str, ingest_event, parse_uuid


logger = logging.getLogger(__name__)


def project_recent_live_recognition_events(session: Session, *, scope_hint: Any | None = None) -> int:
    settings = get_settings()
    if not settings.live_projection_is_enabled:
        return 0

    events = _fetch_recent_recognition_events()
    projected = 0
    for event in events:
        if event.event_type not in SUPPORTED_EVENT_TYPES:
            continue
        if not evidence_ref_profile(event.payload, max_refs=settings.media_resolution_max_refs).has_live:
            continue
        scoped_event = _with_api_scope_from_camera(session, event, scope_hint=scope_hint)
        try:
            result = ingest_event(session, scoped_event)
        except ValueError:
            logger.debug("live_projection_unsupported_event", extra={"event_type": event.event_type})
            continue
        if result.status == "applied":
            projected += 1
    return projected


def _fetch_recent_recognition_events() -> list[RecognitionEventEnvelope]:
    settings = get_settings()
    try:
        payloads = _fetch_outbox_payloads(settings.recognition_database_url, settings.live_projection_max_events)
    except SQLAlchemyError as exc:
        logger.debug("live_projection_outbox_unavailable", extra={"error": str(exc)})
        payloads = []

    events = [_parse_envelope(payload) for payload in payloads]
    parsed = [event for event in events if event is not None]
    if parsed:
        return parsed

    try:
        return _fetch_recognition_table_events(settings.recognition_database_url, settings.live_projection_max_events)
    except SQLAlchemyError as exc:
        logger.debug("live_projection_recognition_table_unavailable", extra={"error": str(exc)})
        return []


def _fetch_outbox_payloads(database_url: str, limit: int) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT aggregate_id, payload
        FROM outbox.event_outbox
        WHERE aggregate_type = 'recognition_event'
        ORDER BY occurred_at DESC
        LIMIT :limit
        """
    )
    with _recognition_engine(database_url).connect() as connection:
        rows = connection.execute(query, {"limit": max(1, limit)}).mappings().all()
    payloads = []
    for row in rows:
        payload = _coerce_payload(row["payload"])
        if row["aggregate_id"]:
            payload["event_id"] = f"evt_recognition_{row['aggregate_id']}"
        payloads.append(payload)
    return payloads


def _fetch_recognition_table_events(database_url: str, limit: int) -> list[RecognitionEventEnvelope]:
    query = text(
        """
        SELECT
            recognition_event_id,
            event_type,
            event_ts,
            severity,
            confidence,
            decision_reason,
            evidence_refs,
            payload,
            camera_id,
            human_track_id,
            observed_subject_id
        FROM recognition.recognition_event
        ORDER BY event_ts DESC
        LIMIT :limit
        """
    )
    with _recognition_engine(database_url).connect() as connection:
        rows = connection.execute(query, {"limit": max(1, limit)}).mappings().all()

    events: list[RecognitionEventEnvelope] = []
    for row in rows:
        payload = _coerce_payload(row["payload"])
        evidence_refs = _coerce_string_list(row["evidence_refs"])
        if evidence_refs:
            payload["evidence_refs"] = evidence_refs
        if "decision_reason" not in payload:
            payload["decision_reason"] = _coerce_string_list(row["decision_reason"])
        if "severity" not in payload:
            payload["severity"] = as_str(row["severity"]) or "low"
        if "confidence" not in payload and row["confidence"] is not None:
            payload["confidence"] = float(row["confidence"])
        event_payload = {
            "event_id": f"evt_recognition_{row['recognition_event_id']}",
            "event_type": as_str(row["event_type"]),
            "event_version": "1.0",
            "occurred_at": row["event_ts"],
            "emitted_at": row["event_ts"],
            "source": {"component": "vigilante-recognition", "instance": "recognition-db", "version": None},
            "payload": payload,
            "context": {
                "camera_id": as_str(row["camera_id"]),
                "track_id": as_str(row["human_track_id"]),
                "subject_id": as_str(row["observed_subject_id"]),
                "idempotency_key": f"recognition-db:{row['recognition_event_id']}",
            },
        }
        parsed = _parse_envelope(event_payload)
        if parsed is not None:
            events.append(parsed)
    return events


def _parse_envelope(payload: dict[str, Any]) -> RecognitionEventEnvelope | None:
    try:
        return RecognitionEventEnvelope.model_validate(payload)
    except ValidationError as exc:
        logger.debug("live_projection_invalid_envelope", extra={"error": str(exc)})
        return None


def _with_api_scope_from_camera(
    session: Session,
    event: RecognitionEventEnvelope,
    *,
    scope_hint: Any | None,
) -> RecognitionEventEnvelope:
    context = dict(event.context or {})
    if context.get("organization_id") and context.get("site_id"):
        return event

    camera_id = parse_uuid(context.get("camera_id"))
    if camera_id is not None:
        camera = session.get(Camera, camera_id)
        if camera is not None:
            if not context.get("site_id") and camera.site_id is not None:
                context["site_id"] = str(camera.site_id)
            if not context.get("organization_id") and camera.site_id is not None:
                site = session.get(Site, camera.site_id)
                if site is not None and site.organization_id is not None:
                    context["organization_id"] = str(site.organization_id)
    if not context.get("organization_id"):
        _apply_scope_hint(context, scope_hint)
    elif not context.get("site_id"):
        _apply_scope_hint(context, scope_hint)
    return event.model_copy(update={"context": context})


def _apply_scope_hint(context: dict[str, Any], scope_hint: Any | None) -> None:
    scopes = list(getattr(scope_hint, "scopes", []) or [])
    for scope in scopes:
        organization_id = as_str(getattr(scope, "organization_id", None))
        site_ids = list(getattr(scope, "site_ids", []) or [])
        if organization_id and not context.get("organization_id"):
            context["organization_id"] = organization_id
        if site_ids and not context.get("site_id"):
            context["site_id"] = str(site_ids[0])
        if context.get("organization_id"):
            return


def _coerce_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return _coerce_string_list(decoded)
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return []


@lru_cache
def _recognition_engine(database_url: str):
    return create_engine(database_url, future=True, pool_pre_ping=True)
