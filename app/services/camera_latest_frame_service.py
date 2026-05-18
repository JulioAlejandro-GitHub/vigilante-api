from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Camera
from app.services.evidence_resolution_service import EvidenceResolutionService
from app.services.media_models import EvidenceMediaItem, sanitize_media_metadata

logger = logging.getLogger(__name__)


class CameraIngestionStateRead(BaseModel):
    camera_id: str
    is_desired_active: bool | None = None
    worker_state: str | None = None
    last_started_at: datetime | None = None
    last_connected_at: datetime | None = None
    last_frame_at: datetime | None = None
    last_publish_at: datetime | None = None
    frames_captured: int | None = None
    events_published: int | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class CameraLatestFrameRead(BaseModel):
    camera_id: str
    latest_frame_ref: str | None = None
    latest_frame_at: datetime | None = None
    frame_age_seconds: float | None = None
    event_id: str | None = None
    content_type: str | None = None
    width: int | None = None
    height: int | None = None
    state: str = "no_frame_yet"
    reason: str | None = None
    media: EvidenceMediaItem | None = None
    ingestion: CameraIngestionStateRead | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _OutboxFrame:
    camera_id: str
    frame_ref: str
    captured_at: datetime
    event_id: str | None
    content_type: str | None
    width: int | None
    height: int | None
    metadata: dict[str, Any]


@dataclass
class _OutboxCache:
    path: Path | None = None
    mtime_ns: int | None = None
    size: int | None = None
    frames: dict[str, _OutboxFrame] | None = None


_OUTBOX_CACHE = _OutboxCache()


def list_camera_latest_frames(
    session: Session,
    *,
    camera_ids: list[str],
    evidence_resolution: EvidenceResolutionService,
    include_media: bool = True,
    settings: Settings | None = None,
) -> list[CameraLatestFrameRead]:
    resolved_settings = settings or get_settings()
    cameras = _load_cameras(session, camera_ids)
    outbox_path = _resolve_outbox_path(resolved_settings.ingestion_outbox_path)
    latest_by_camera = _load_latest_outbox_frames(
        outbox_path,
        max_lines=max(1, resolved_settings.ingestion_outbox_tail_max_lines),
        max_bytes=max(1024, resolved_settings.ingestion_outbox_tail_max_bytes),
    )
    ingestion_by_camera = _load_ingestion_health(resolved_settings)
    now = datetime.now(timezone.utc)
    results: list[CameraLatestFrameRead] = []
    requested_media = 0

    for camera_id in camera_ids:
        camera = cameras.get(camera_id)
        frame = latest_by_camera.get(camera_id)
        ingestion = ingestion_by_camera.get(camera_id)
        media = None
        if include_media and frame is not None:
            requested_media += 1
            media_items = evidence_resolution.resolve_refs([frame.frame_ref])
            media = media_items[0] if media_items else None

        results.append(_build_latest_frame_read(camera_id, camera=camera, frame=frame, media=media, ingestion=ingestion, now=now))

    logger.info(
        "camera_latest_frames_loaded cameras=%s frames=%s media_requested=%s media_resolved=%s media_failed=%s health=%s outbox=%s",
        len(camera_ids),
        sum(1 for item in results if item.latest_frame_ref),
        requested_media,
        evidence_resolution.stats.resolved,
        evidence_resolution.stats.failed,
        len(ingestion_by_camera),
        outbox_path,
    )
    return results


def _load_cameras(session: Session, camera_ids: list[str]) -> dict[str, Camera]:
    cameras: dict[str, Camera] = {}
    for camera_id in camera_ids:
        try:
            parsed_camera_id = UUID(camera_id)
        except ValueError:
            continue
        camera = session.get(Camera, parsed_camera_id)
        if camera is not None:
            cameras[str(camera.camera_id)] = camera
    return cameras


def _build_latest_frame_read(
    camera_id: str,
    *,
    camera: Camera | None,
    frame: _OutboxFrame | None,
    media: EvidenceMediaItem | None,
    ingestion: CameraIngestionStateRead | None,
    now: datetime,
) -> CameraLatestFrameRead:
    state, reason = _frame_state(camera=camera, frame=frame, ingestion=ingestion, now=now)
    if frame is None:
        return CameraLatestFrameRead(camera_id=camera_id, state=state, reason=reason, ingestion=ingestion)

    age = max(0.0, (now - frame.captured_at).total_seconds())
    return CameraLatestFrameRead(
        camera_id=camera_id,
        latest_frame_ref=frame.frame_ref,
        latest_frame_at=frame.captured_at,
        frame_age_seconds=age,
        event_id=frame.event_id,
        content_type=frame.content_type,
        width=frame.width,
        height=frame.height,
        state=state,
        reason=reason,
        media=media,
        ingestion=ingestion,
        metadata=sanitize_media_metadata(frame.metadata),
    )


def _frame_state(
    *,
    camera: Camera | None,
    frame: _OutboxFrame | None,
    ingestion: CameraIngestionStateRead | None,
    now: datetime,
) -> tuple[str, str | None]:
    if camera is not None and not camera.is_active:
        return "offline", "camera_disabled"

    worker_state = (ingestion.worker_state or "").lower() if ingestion is not None else ""
    if worker_state == "failed":
        return "degraded", ingestion.last_error or "ingestion_worker_failed"
    if worker_state == "stopped" and ingestion is not None and ingestion.is_desired_active and not ingestion.last_started_at:
        return "not_started_concurrency", ingestion.last_error or "not_started_by_concurrency_limit"

    if frame is None:
        if worker_state in {"starting", "connecting", "retrying"}:
            return "degraded", f"ingestion_worker_{worker_state}"
        if worker_state == "stopped" and ingestion is not None and ingestion.is_desired_active:
            return "not_started_concurrency", ingestion.last_error or "not_started_by_concurrency_limit"
        return "no_frame_yet", "no_ingested_frame"

    age = max(0.0, (now - frame.captured_at).total_seconds())
    if age <= 5:
        return "live", None
    if age <= 60:
        return "online", None
    if age <= 5 * 60:
        return "stale", "latest_frame_is_stale"
    return "offline", "latest_frame_too_old"


def _load_latest_outbox_frames(path: Path, *, max_lines: int, max_bytes: int) -> dict[str, _OutboxFrame]:
    resolved_path = path.expanduser().resolve()
    try:
        stat = resolved_path.stat()
    except OSError:
        logger.debug("camera_latest_frames_outbox_missing path=%s", resolved_path)
        return {}

    if (
        _OUTBOX_CACHE.path == resolved_path
        and _OUTBOX_CACHE.mtime_ns == stat.st_mtime_ns
        and _OUTBOX_CACHE.size == stat.st_size
        and _OUTBOX_CACHE.frames is not None
    ):
        return dict(_OUTBOX_CACHE.frames)

    lines = _tail_lines(resolved_path, max_lines=max_lines, max_bytes=max_bytes)
    frames: dict[str, _OutboxFrame] = {}
    invalid_lines = 0
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        frame = _frame_from_event(event)
        if frame is None:
            continue
        current = frames.get(frame.camera_id)
        if current is None or frame.captured_at >= current.captured_at:
            frames[frame.camera_id] = frame

    _OUTBOX_CACHE.path = resolved_path
    _OUTBOX_CACHE.mtime_ns = stat.st_mtime_ns
    _OUTBOX_CACHE.size = stat.st_size
    _OUTBOX_CACHE.frames = dict(frames)
    logger.debug(
        "camera_latest_frames_outbox_loaded path=%s lines=%s invalid_lines=%s cameras=%s",
        resolved_path,
        len(lines),
        invalid_lines,
        len(frames),
    )
    return frames


def _tail_lines(path: Path, *, max_lines: int, max_bytes: int) -> list[str]:
    if max_lines <= 0:
        return []
    block_size = 8192
    chunks: list[bytes] = []
    bytes_read = 0
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        while position > 0 and bytes_read < max_bytes:
            read_size = min(block_size, position, max_bytes - bytes_read)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            bytes_read += len(chunk)
            if b"".join(reversed(chunks)).count(b"\n") > max_lines:
                break
    payload = b"".join(reversed(chunks))
    return [line.decode("utf-8", errors="replace") for line in payload.splitlines() if line][-max_lines:]


def _frame_from_event(event: dict[str, Any]) -> _OutboxFrame | None:
    event_type = _as_text(event.get("event_type") or event.get("type"))
    if event_type not in {"frame.ingested", "frame_ingested"}:
        return None
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    camera_id = _as_text(payload.get("camera_id") or context.get("camera_id") or event.get("camera_id"))
    frame_ref = _as_text(payload.get("frame_ref") or payload.get("frame_uri") or payload.get("source_frame_ref") or event.get("frame_ref"))
    captured_at = _parse_datetime(
        payload.get("captured_at")
        or payload.get("frame_captured_at")
        or payload.get("timestamp")
        or event.get("occurred_at")
        or event.get("emitted_at")
        or event.get("created_at")
    )
    if not camera_id or not frame_ref or captured_at is None:
        return None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return _OutboxFrame(
        camera_id=camera_id,
        frame_ref=frame_ref,
        captured_at=captured_at,
        event_id=_as_text(event.get("event_id")),
        content_type=_as_text(payload.get("content_type")),
        width=_as_int(payload.get("width")),
        height=_as_int(payload.get("height")),
        metadata=dict(metadata),
    )


def _resolve_outbox_path(raw_path: str) -> Path:
    configured = Path(raw_path).expanduser()
    if configured.is_absolute():
        return configured

    candidates = [
        Path.cwd() / configured,
        Path(__file__).resolve().parents[2] / configured,
        Path(__file__).resolve().parents[3] / configured,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[1]


def _load_ingestion_health(settings: Settings) -> dict[str, CameraIngestionStateRead]:
    if not settings.ingestion_health_base_url:
        return {}
    base_url = settings.ingestion_health_base_url.rstrip("/")
    try:
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(settings.ingestion_health_timeout_seconds)) as client:
            response = client.get("/health/cameras")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("camera_latest_frames_ingestion_health_unavailable error=%s", str(exc))
        return {}
    if not isinstance(payload, list):
        return {}
    states: dict[str, CameraIngestionStateRead] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        camera_id = _as_text(item.get("camera_id"))
        if not camera_id:
            continue
        try:
            states[camera_id] = CameraIngestionStateRead.model_validate(item)
        except ValueError:
            logger.debug("camera_latest_frames_ingestion_health_invalid camera_id=%s", camera_id)
    return states


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
