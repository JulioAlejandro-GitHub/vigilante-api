#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.services.camera_config_service import SENSITIVE_METADATA_KEYS
from app.services.camera_secret_service import (
    decrypt_camera_secret,
    encrypt_camera_secret,
    is_encrypted_camera_secret,
    is_legacy_plain_camera_secret,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Encrypt legacy api.camera.camera_secret values and scrub legacy metadata secrets.")
    parser.add_argument("--dry-run", action="store_true", help="Report rows that would change without writing updates.")
    args = parser.parse_args()

    settings = get_settings()
    table_name = f"{settings.api_schema}.camera" if settings.api_schema else "camera"
    changed = 0
    scanned = 0

    with get_engine().begin() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT camera_id, camera_secret, metadata
                FROM {table_name}
                WHERE camera_secret IS NOT NULL
                   OR metadata ? 'camera_pass'
                   OR metadata ? 'camera_secret'
                   OR metadata ? 'stream_url'
                """
                if not settings.is_sqlite
                else f"""
                SELECT camera_id, camera_secret, metadata
                FROM {table_name}
                """
            )
        ).mappings()

        for row in rows:
            scanned += 1
            metadata = _metadata_to_dict(row["metadata"])
            plaintext = _resolve_plaintext_secret(row["camera_secret"], metadata)
            encrypted_secret = encrypt_camera_secret(plaintext) if plaintext else row["camera_secret"]
            sanitized_metadata = _sanitize_metadata_for_persistence(metadata)

            if encrypted_secret == row["camera_secret"] and sanitized_metadata == metadata:
                continue

            changed += 1
            if args.dry_run:
                continue

            connection.execute(
                text(
                    f"""
                    UPDATE {table_name}
                    SET camera_secret = :camera_secret,
                        metadata = CAST(:metadata AS jsonb)
                    WHERE camera_id = :camera_id
                    """
                    if not settings.is_sqlite
                    else f"""
                    UPDATE {table_name}
                    SET camera_secret = :camera_secret,
                        metadata = :metadata
                    WHERE camera_id = :camera_id
                    """
                ),
                {
                    "camera_id": row["camera_id"],
                    "camera_secret": encrypted_secret,
                    "metadata": json.dumps(sanitized_metadata, sort_keys=True),
                },
            )

    action = "would_update" if args.dry_run else "updated"
    print(f"camera_secret_backfill scanned={scanned} {action}={changed}")
    return 0


def _resolve_plaintext_secret(camera_secret: str | None, metadata: dict[str, Any]) -> str | None:
    if is_encrypted_camera_secret(camera_secret):
        return decrypt_camera_secret(camera_secret)
    if is_legacy_plain_camera_secret(camera_secret):
        return camera_secret
    for key in ("camera_pass", "camera_secret", "rtsp_password", "password"):
        value = metadata.get(key)
        if value:
            return str(value)
    stream_url = metadata.get("stream_url")
    if isinstance(stream_url, str):
        parsed = urlparse(stream_url)
        if parsed.password:
            return parsed.password
    return None


def _sanitize_metadata_for_persistence(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = key.lower()
        if normalized_key in SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(value, dict):
            sanitized[key] = _sanitize_metadata_for_persistence(value)
        elif isinstance(value, list):
            sanitized[key] = [_sanitize_metadata_value(item) for item in value]
        elif normalized_key.endswith("url") or normalized_key.endswith("uri"):
            sanitized[key] = _mask_rtsp_credentials(str(value))
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_metadata_for_persistence(value)
    if isinstance(value, list):
        return [_sanitize_metadata_value(item) for item in value]
    if isinstance(value, str):
        return _mask_rtsp_credentials(value)
    return value


def _mask_rtsp_credentials(value: str) -> str:
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


def _metadata_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


if __name__ == "__main__":
    raise SystemExit(main())
