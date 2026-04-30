from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MediaAssetResponse(BaseModel):
    media_id: str
    media_type: str | None = None
    storage_backend: str | None = None
    bucket: str | None = None
    object_key: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    captured_at: datetime | None = None
    last_modified_at: datetime | None = None
    camera_id: str | None = None
    checksum_sha256: str | None = None
    etag: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_ref: str | None = None
    content_url: str | None = None
    thumbnail_url: str | None = None
    thumbnail_content_type: str | None = None
    thumbnail_width: int | None = None
    thumbnail_height: int | None = None
    thumbnail_available: bool = False
    thumbnail_status: str | None = None
    clip_available: bool = False
    clip_status: str | None = None
    clip_url: str | None = None
    clip_content_type: str | None = None
    clip_duration_seconds: float | None = None
    clip_frame_count: int | None = None
    clip_fps: float | None = None
    clip_width: int | None = None
    clip_height: int | None = None
    metadata_url: str | None = None


class EvidenceMediaItem(BaseModel):
    ref: str
    resolved: bool = False
    media_id: str | None = None
    media_type: str | None = None
    storage_backend: str | None = None
    bucket: str | None = None
    object_key: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    captured_at: datetime | None = None
    last_modified_at: datetime | None = None
    camera_id: str | None = None
    checksum_sha256: str | None = None
    etag: str | None = None
    content_url: str | None = None
    thumbnail_url: str | None = None
    thumbnail_content_type: str | None = None
    thumbnail_width: int | None = None
    thumbnail_height: int | None = None
    thumbnail_available: bool = False
    thumbnail_status: str | None = None
    clip_available: bool = False
    clip_status: str | None = None
    clip_url: str | None = None
    clip_content_type: str | None = None
    clip_duration_seconds: float | None = None
    clip_frame_count: int | None = None
    clip_fps: float | None = None
    clip_width: int | None = None
    clip_height: int | None = None
    proxy_url: str | None = None
    metadata_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @classmethod
    def from_asset(cls, ref: str, asset: MediaAssetResponse) -> "EvidenceMediaItem":
        metadata = sanitize_media_metadata(asset.metadata)
        return cls(
            ref=ref,
            resolved=True,
            media_id=asset.media_id,
            media_type=asset.media_type,
            storage_backend=asset.storage_backend,
            bucket=asset.bucket,
            object_key=asset.object_key,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
            width=_coerce_int(_first_metadata_value(metadata, "width", "image_width")),
            height=_coerce_int(_first_metadata_value(metadata, "height", "image_height")),
            captured_at=asset.captured_at,
            last_modified_at=asset.last_modified_at,
            camera_id=asset.camera_id,
            checksum_sha256=asset.checksum_sha256,
            etag=asset.etag,
            content_url=asset.content_url,
            thumbnail_url=asset.thumbnail_url,
            thumbnail_content_type=asset.thumbnail_content_type,
            thumbnail_width=asset.thumbnail_width,
            thumbnail_height=asset.thumbnail_height,
            thumbnail_available=asset.thumbnail_available,
            thumbnail_status=asset.thumbnail_status,
            clip_available=asset.clip_available,
            clip_status=asset.clip_status,
            clip_url=asset.clip_url,
            clip_content_type=asset.clip_content_type,
            clip_duration_seconds=asset.clip_duration_seconds,
            clip_frame_count=asset.clip_frame_count,
            clip_fps=asset.clip_fps,
            clip_width=asset.clip_width,
            clip_height=asset.clip_height,
            metadata_url=asset.metadata_url,
            metadata=metadata,
        )

    @classmethod
    def unresolved(cls, ref: str, error: str) -> "EvidenceMediaItem":
        return cls(ref=ref, resolved=False, error=error)


SECRET_METADATA_MARKERS = (
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "access_key",
    "access-key",
    "private_key",
    "private-key",
    "authorization",
)


def sanitize_media_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = str(key).lower()
        if any(marker in normalized_key for marker in SECRET_METADATA_MARKERS):
            continue
        clean[str(key)] = _sanitize_metadata_value(value)
    return clean


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return sanitize_media_metadata(value)
    if isinstance(value, list):
        return [_sanitize_metadata_value(item) for item in value]
    return value


def _first_metadata_value(metadata: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metadata:
            return metadata[key]
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
