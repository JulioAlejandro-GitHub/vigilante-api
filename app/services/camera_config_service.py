from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Camera


SENSITIVE_METADATA_KEYS = {
    "camera_pass",
    "camera_password",
    "camera_secret",
    "password",
    "pass",
    "secret",
    "rtsp_password",
}


class CameraRead(BaseModel):
    camera_id: str
    external_camera_key: str | None = None
    site_id: str | None = None
    source_type: str | None = None
    camera_hostname: str | None = None
    camera_port: int | None = Field(default=None, ge=1, le=65535)
    camera_path: str | None = None
    rtsp_transport: str | None = None
    channel: int | None = None
    subtype: int | None = None
    camera_user: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def list_cameras(session: Session, *, limit: int) -> list[CameraRead]:
    rows = session.scalars(select(Camera).order_by(Camera.camera_id.asc()).limit(limit)).all()
    return [camera_to_read(row) for row in rows]


def get_camera(session: Session, camera_id: str) -> CameraRead | None:
    try:
        parsed_camera_id = UUID(camera_id)
    except ValueError:
        return None
    row = session.get(Camera, parsed_camera_id)
    if row is None:
        return None
    return camera_to_read(row)


def camera_to_read(camera: Camera) -> CameraRead:
    return CameraRead(
        camera_id=str(camera.camera_id),
        external_camera_key=camera.external_camera_key,
        site_id=str(camera.site_id) if camera.site_id else None,
        source_type=camera.source_type,
        camera_hostname=camera.camera_hostname,
        camera_port=camera.camera_port,
        camera_path=camera.camera_path,
        rtsp_transport=camera.rtsp_transport,
        channel=camera.channel,
        subtype=camera.subtype,
        camera_user=camera.camera_user,
        metadata=sanitize_camera_metadata(camera.camera_metadata or {}),
    )


def sanitize_camera_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = key.lower()
        if normalized_key in SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(value, dict):
            sanitized[key] = sanitize_camera_metadata(value)
        elif isinstance(value, list):
            sanitized[key] = [_sanitize_metadata_value(item) for item in value]
        elif normalized_key.endswith("url") or normalized_key.endswith("uri"):
            sanitized[key] = mask_rtsp_credentials(str(value))
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return sanitize_camera_metadata(value)
    if isinstance(value, list):
        return [_sanitize_metadata_value(item) for item in value]
    if isinstance(value, str):
        return mask_rtsp_credentials(value)
    return value


def mask_rtsp_credentials(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"rtsp", "rtsps"} or parsed.password is None:
        return value

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    username = parsed.username or ""
    netloc = f"{username}:***@{host}" if username else host
    return urlunparse(parsed._replace(netloc=netloc))
