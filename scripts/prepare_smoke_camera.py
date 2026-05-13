from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.db import get_engine
from app.services.camera_secret_service import encrypt_camera_secret_if_plaintext


DEFAULT_CAMERA_ID = UUID("11111111-1111-1111-1111-111111111111")
DEFAULT_EXTERNAL_CAMERA_KEY = "smoke_rtsp_local_001"
DEFAULT_CAMERA_NAME = "Smoke Ready Camera"
DEFAULT_RTSP_URL = "rtsp://127.0.0.1:8554/cam01"
DEFAULT_RTSP_TRANSPORT = "tcp"
SMOKE_METADATA_SOURCE = "prepare_smoke_camera.py"

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

INGESTION_REQUIRED_COLUMNS = {
    "camera_id",
    "external_camera_key",
    "site_id",
    "zone_id",
    "name",
    "is_active",
    "source_type",
    "camera_hostname",
    "camera_port",
    "camera_path",
    "rtsp_transport",
    "channel",
    "subtype",
    "camera_user",
    "camera_secret",
    "metadata",
}


class SmokeCameraSetupError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {json.dumps(self.details, sort_keys=True, default=str)}")


@dataclass(frozen=True)
class SmokeSite:
    user_id: Any
    username: str
    organization_id: Any
    site_id: Any


@dataclass(frozen=True)
class SmokeCameraResult:
    camera_id: str
    external_camera_key: str
    site_id: str
    organization_id: str
    username: str
    source_type: str
    is_active: bool
    rtsp_url: str
    action: str
    state_file: str | None = None

    def as_kv_lines(self) -> list[str]:
        return [
            "smoke_camera_ready=true",
            f"camera_id={self.camera_id}",
            f"external_camera_key={self.external_camera_key}",
            f"site_id={self.site_id}",
            f"organization_id={self.organization_id}",
            f"username={self.username}",
            f"source_type={self.source_type}",
            f"is_active={str(self.is_active).lower()}",
            f"rtsp_url={self.rtsp_url}",
            f"action={self.action}",
            f"state_file={self.state_file or ''}",
        ]


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = prepare_smoke_camera(args)
    except SmokeCameraSetupError as exc:
        print(f"ERROR: {exc.code} details={json.dumps(exc.details, sort_keys=True, default=str)}")
        return 2
    for line in result.as_kv_lines():
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or align one local smoke-ready RTSP camera.")
    parser.add_argument("--username", default=os.getenv("SMOKE_CAMERA_USERNAME", os.getenv("DEMO_USER", "julio")))
    parser.add_argument("--camera-id", default=os.getenv("SMOKE_CAMERA_ID", str(DEFAULT_CAMERA_ID)))
    parser.add_argument("--external-camera-key", default=os.getenv("SMOKE_CAMERA_EXTERNAL_KEY", DEFAULT_EXTERNAL_CAMERA_KEY))
    parser.add_argument("--name", default=os.getenv("SMOKE_CAMERA_NAME", DEFAULT_CAMERA_NAME))
    parser.add_argument("--rtsp-url", default=os.getenv("SMOKE_CAMERA_RTSP_URL", DEFAULT_RTSP_URL))
    parser.add_argument("--rtsp-transport", default=os.getenv("SMOKE_CAMERA_RTSP_TRANSPORT", DEFAULT_RTSP_TRANSPORT))
    parser.add_argument("--site-id", default=os.getenv("SMOKE_CAMERA_SITE_ID"))
    parser.add_argument("--organization-id", default=os.getenv("SMOKE_CAMERA_ORGANIZATION_ID"))
    parser.add_argument("--camera-user", default=os.getenv("SMOKE_CAMERA_RTSP_USER"))
    parser.add_argument("--camera-secret", default=os.getenv("SMOKE_CAMERA_RTSP_PASSWORD"))
    parser.add_argument("--state-file", default=os.getenv("SMOKE_CAMERA_STATE_FILE"))
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="Only validate/update an existing smoke camera; fail if it does not exist.",
    )
    parser.add_argument(
        "--skip-ingestion-schema-check",
        action="store_true",
        help="Skip the active_cameras column check. Intended only for narrow tests.",
    )
    return parser


def prepare_smoke_camera(args: argparse.Namespace) -> SmokeCameraResult:
    settings = get_settings()
    api_schema = settings.api_schema
    auth_schema = settings.auth_schema
    if api_schema:
        _validate_identifier(api_schema)
    if auth_schema:
        _validate_identifier(auth_schema)

    camera_id = str(UUID(str(args.camera_id)))
    rtsp = parse_rtsp_url(args.rtsp_url)
    if (args.rtsp_transport or "").lower() not in {"tcp", "udp"}:
        raise SmokeCameraSetupError(
            "smoke_camera_not_active_in_ingestion",
            {"reason": "rtsp_transport_must_be_tcp_or_udp", "rtsp_transport": args.rtsp_transport},
        )

    with get_engine().begin() as connection:
        tables = reflect_required_tables(connection, api_schema=api_schema, auth_schema=auth_schema)
        camera_table = tables["camera"]
        if not args.skip_ingestion_schema_check:
            assert_ingestion_columns(camera_table)

        smoke_site = resolve_smoke_site(
            connection,
            tables=tables,
            username=args.username,
            requested_site_id=args.site_id,
            requested_organization_id=args.organization_id,
        )
        existing = find_target_camera(connection, camera_table, camera_id=camera_id, external_camera_key=args.external_camera_key)
        if existing is None and args.no_create:
            raise SmokeCameraSetupError(
                "smoke_camera_not_found",
                {"camera_id": camera_id, "external_camera_key": args.external_camera_key},
            )

        metadata = build_smoke_metadata(
            _json_object(existing.get("metadata")) if existing else {},
            external_camera_key=args.external_camera_key,
            rtsp_url=args.rtsp_url,
        )
        values = build_camera_values(
            camera_table,
            camera_id=camera_id,
            external_camera_key=args.external_camera_key,
            name=args.name,
            smoke_site=smoke_site,
            rtsp=rtsp,
            rtsp_transport=args.rtsp_transport,
            camera_user=args.camera_user,
            camera_secret=args.camera_secret,
            metadata=metadata,
            existing=existing,
        )
        values = coerce_uuid_values_for_dialect(connection, camera_table, values)

        if existing:
            target_camera_id = existing["camera_id"]
            connection.execute(
                camera_table.update().where(camera_table.c.camera_id == target_camera_id).values(**values)
            )
            action = "updated_existing"
        else:
            insert_values = dict(values)
            insert_values["camera_id"] = bind_uuid(connection, camera_id)
            missing = missing_required_insert_columns(camera_table, insert_values)
            if missing:
                raise SmokeCameraSetupError(
                    "smoke_camera_not_found",
                    {"reason": "cannot_create_camera_missing_required_values", "missing_columns": missing},
                )
            connection.execute(camera_table.insert().values(**insert_values))
            target_camera_id = bind_uuid(connection, camera_id)
            action = "created"

        row = get_camera_by_id(connection, camera_table, target_camera_id)
        if row is None:
            raise SmokeCameraSetupError("smoke_camera_not_found", {"camera_id": str(target_camera_id)})
        assert_prepared_camera(row, smoke_site=smoke_site)

    result = SmokeCameraResult(
        camera_id=uuid_text(row["camera_id"]),
        external_camera_key=str(row["external_camera_key"]),
        site_id=uuid_text(row["site_id"]),
        organization_id=uuid_text(smoke_site.organization_id),
        username=smoke_site.username,
        source_type=str(row["source_type"]),
        is_active=bool(row["is_active"]),
        rtsp_url=args.rtsp_url,
        action=action,
        state_file=args.state_file,
    )
    if args.state_file:
        write_state_file(Path(args.state_file), result)
    return result


def reflect_required_tables(connection: Connection, *, api_schema: str | None, auth_schema: str | None) -> dict[str, Table]:
    metadata = MetaData()
    table_specs = {
        "camera": (api_schema, "camera"),
        "site": (api_schema, "site"),
        "app_user": (auth_schema, "app_user"),
        "user_organization_scope": (auth_schema, "user_organization_scope"),
    }
    tables: dict[str, Table] = {}
    for logical_name, (schema, table_name) in table_specs.items():
        if not inspect(connection).has_table(table_name, schema=schema):
            raise SmokeCameraSetupError(
                "smoke_camera_not_found",
                {"reason": "required_table_missing", "table": f"{schema + '.' if schema else ''}{table_name}"},
            )
        tables[logical_name] = Table(table_name, metadata, schema=schema, autoload_with=connection)
    return tables


def assert_ingestion_columns(camera_table: Table) -> None:
    missing = sorted(column for column in INGESTION_REQUIRED_COLUMNS if column not in camera_table.c)
    if missing:
        raise SmokeCameraSetupError(
            "smoke_camera_not_active_in_ingestion",
            {
                "reason": "api_camera_missing_columns_required_by_active_cameras_loader",
                "missing_columns": missing,
            },
        )


def resolve_smoke_site(
    connection: Connection,
    *,
    tables: dict[str, Table],
    username: str,
    requested_site_id: str | None,
    requested_organization_id: str | None,
) -> SmokeSite:
    user = find_user(connection, tables["app_user"], username)
    if user is None:
        raise SmokeCameraSetupError(
            "smoke_camera_permission_mismatch",
            {"reason": "smoke_user_not_found_or_inactive", "username": username},
        )

    scopes = connection.execute(
        select(tables["user_organization_scope"]).where(tables["user_organization_scope"].c.user_id == user["user_id"])
    ).mappings()
    for scope in scopes:
        if not _truthy(scope.get("can_view")):
            continue
        organization_id = scope.get("organization_id")
        if requested_organization_id and str(organization_id) != str(UUID(requested_organization_id)):
            continue
        site_ids = site_ids_for_scope(connection, tables["site"], scope)
        if requested_site_id:
            requested_uuid = str(UUID(requested_site_id))
            site_ids = [site_id for site_id in site_ids if str(site_id) == requested_uuid]
        if site_ids:
            return SmokeSite(
                user_id=user["user_id"],
                username=username,
                organization_id=organization_id,
                site_id=site_ids[0],
            )

    raise SmokeCameraSetupError(
        "smoke_camera_permission_mismatch",
        {
            "reason": "no_visible_site_for_smoke_user",
            "username": username,
            "requested_site_id": requested_site_id,
            "requested_organization_id": requested_organization_id,
        },
    )


def find_user(connection: Connection, app_user: Table, username: str) -> dict[str, Any] | None:
    users = connection.execute(select(app_user)).mappings()
    normalized = username.strip().lower()
    for row in users:
        if not _truthy(row.get("is_active")) or str(row.get("status", "active")).lower() != "active":
            continue
        metadata = _json_object(row.get("metadata"))
        candidates = {
            str(metadata.get("username") or "").lower(),
            str(row.get("email") or "").lower(),
            str(row.get("email") or "").split("@", 1)[0].lower(),
        }
        if normalized in candidates:
            return dict(row)
    return None


def site_ids_for_scope(connection: Connection, site_table: Table, scope: dict[str, Any]) -> list[Any]:
    metadata = _json_object(scope.get("metadata"))
    raw_site_ids = metadata.get("site_ids")
    if isinstance(raw_site_ids, list) and raw_site_ids:
        return [UUID(str(site_id)) for site_id in raw_site_ids]

    rows = connection.execute(
        select(site_table.c.site_id).where(site_table.c.organization_id == scope["organization_id"]).order_by(site_table.c.site_id.asc())
    )
    return [row[0] for row in rows]


def find_target_camera(
    connection: Connection,
    camera_table: Table,
    *,
    camera_id: str,
    external_camera_key: str,
) -> dict[str, Any] | None:
    rows = [dict(row) for row in connection.execute(select(camera_table)).mappings()]
    by_external_key = next((row for row in rows if str(row.get("external_camera_key")) == external_camera_key), None)
    if by_external_key is not None:
        return by_external_key
    return next((row for row in rows if uuid_text(row.get("camera_id")) == camera_id), None)


def get_camera_by_id(connection: Connection, camera_table: Table, camera_id: Any) -> dict[str, Any] | None:
    row = connection.execute(select(camera_table).where(camera_table.c.camera_id == camera_id)).mappings().first()
    return dict(row) if row else None


def parse_rtsp_url(value: str) -> dict[str, Any]:
    parsed = urlparse(value)
    if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname or not parsed.path:
        raise SmokeCameraSetupError("smoke_camera_not_active_in_ingestion", {"reason": "invalid_rtsp_url", "rtsp_url": value})
    return {
        "hostname": parsed.hostname,
        "port": parsed.port or 554,
        "path": parsed.path,
        "username": parsed.username,
        "password": parsed.password,
    }


def build_camera_values(
    camera_table: Table,
    *,
    camera_id: str,
    external_camera_key: str,
    name: str,
    smoke_site: SmokeSite,
    rtsp: dict[str, Any],
    rtsp_transport: str,
    camera_user: str | None,
    camera_secret: str | None,
    metadata: dict[str, Any],
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    effective_camera_user = camera_user or rtsp.get("username")
    effective_camera_secret = camera_secret or rtsp.get("password")
    values_by_column: dict[str, Any] = {
        "external_camera_key": external_camera_key,
        "site_id": smoke_site.site_id,
        "organization_id": smoke_site.organization_id,
        "name": name,
        "profile_type": "general",
        "sensitivity": "normal",
        "is_active": True,
        "status": "active",
        "source_type": "rtsp",
        "camera_hostname": rtsp["hostname"],
        "camera_port": rtsp["port"],
        "camera_path": rtsp["path"],
        "rtsp_transport": rtsp_transport.lower(),
        "channel": None,
        "subtype": None,
        "camera_user": effective_camera_user,
        "camera_secret": encrypted_secret(effective_camera_secret) if effective_camera_secret else None,
        "metadata": metadata,
    }
    if existing is None:
        values_by_column["camera_id"] = UUID(camera_id)

    return {column: value for column, value in values_by_column.items() if column in camera_table.c}


def encrypted_secret(value: str) -> str:
    try:
        return encrypt_camera_secret_if_plaintext(value) or value
    except Exception as exc:
        raise SmokeCameraSetupError(
            "smoke_camera_not_active_in_ingestion",
            {"reason": "camera_secret_encryption_failed", "error": str(exc)},
        ) from exc


def build_smoke_metadata(existing: dict[str, Any], *, external_camera_key: str, rtsp_url: str) -> dict[str, Any]:
    metadata = json.loads(json.dumps(existing))
    recognition = metadata.setdefault("recognition", {})
    if not isinstance(recognition, dict):
        recognition = {}
        metadata["recognition"] = recognition
    recognition["version"] = "ops-v1"
    recognition["enabled"] = True

    face_tuning = recognition.setdefault("face_tuning", {})
    if not isinstance(face_tuning, dict):
        face_tuning = {}
        recognition["face_tuning"] = face_tuning
    face_tuning.update(
        {
            "det_size": "320,320",
            "detection_threshold": 0.65,
            "face_quality_threshold": 0.75,
            "max_faces": 2,
        }
    )

    vlm_policy = recognition.setdefault("vlm_policy", {})
    if not isinstance(vlm_policy, dict):
        vlm_policy = {}
        recognition["vlm_policy"] = vlm_policy
    vlm_policy.update(
        {
            "enabled": True,
            "backend": "simple",
            "preferred_backend": "simple",
            "secondary_backend": "simple",
        }
    )

    smoke = metadata.setdefault("smoke", {})
    if not isinstance(smoke, dict):
        smoke = {}
        metadata["smoke"] = smoke
    smoke.update(
        {
            "is_smoke_ready": True,
            "external_camera_key": external_camera_key,
            "source": SMOKE_METADATA_SOURCE,
            "rtsp_expected": True,
        }
    )
    metadata.setdefault("source_type", "rtsp")
    metadata.setdefault("stream_url", rtsp_url)
    metadata.setdefault("rtsp_transport", DEFAULT_RTSP_TRANSPORT)
    return metadata


def assert_prepared_camera(row: dict[str, Any], *, smoke_site: SmokeSite) -> None:
    metadata = _json_object(row.get("metadata"))
    if not metadata.get("smoke", {}).get("is_smoke_ready"):
        raise SmokeCameraSetupError("smoke_camera_not_found", {"reason": "metadata_smoke_flag_missing"})
    if uuid_text(row.get("site_id")) != uuid_text(smoke_site.site_id):
        raise SmokeCameraSetupError(
            "smoke_camera_not_visible_in_api",
            {
                "reason": "camera_site_not_in_smoke_user_scope",
                "camera_site_id": uuid_text(row.get("site_id")),
                "expected_site_id": uuid_text(smoke_site.site_id),
            },
        )
    if not _truthy(row.get("is_active")) or str(row.get("source_type", "")).lower() != "rtsp":
        raise SmokeCameraSetupError(
            "smoke_camera_not_active_in_ingestion",
            {
                "is_active": row.get("is_active"),
                "source_type": row.get("source_type"),
                "camera_id": str(row.get("camera_id")),
            },
        )
    if _nested(metadata, "recognition", "face_tuning", "face_quality_threshold") is None:
        raise SmokeCameraSetupError(
            "smoke_camera_not_found",
            {"reason": "recognition_face_quality_threshold_missing", "camera_id": str(row.get("camera_id"))},
        )


def missing_required_insert_columns(camera_table: Table, values: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for column in camera_table.columns:
        if column.name in values:
            continue
        if column.nullable or column.default is not None or column.server_default is not None or column.autoincrement:
            continue
        missing.append(column.name)
    return sorted(missing)


def write_state_file(path: Path, result: SmokeCameraResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"SMOKE_CAMERA_ID={result.camera_id}",
                f"VIGILANTE_RECOMMENDATION_CAMERA_ID={result.camera_id}",
                f"REAL_CAMERA_ID={result.camera_id}",
                f"SMOKE_CAMERA_EXTERNAL_KEY={result.external_camera_key}",
                f"SMOKE_CAMERA_SITE_ID={result.site_id}",
                f"SMOKE_CAMERA_USERNAME={result.username}",
                f"SMOKE_CAMERA_RTSP_URL={result.rtsp_url}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def coerce_uuid_values_for_dialect(connection: Connection, table: Table, values: dict[str, Any]) -> dict[str, Any]:
    if connection.dialect.name != "sqlite":
        return values
    coerced = dict(values)
    for column_name in ("camera_id", "site_id", "zone_id", "organization_id"):
        if column_name in table.c and column_name in coerced and coerced[column_name] is not None:
            coerced[column_name] = UUID(str(coerced[column_name])).hex
    return coerced


def bind_uuid(connection: Connection, value: Any) -> Any:
    uuid_value = UUID(str(value))
    if connection.dialect.name == "sqlite":
        return uuid_value.hex
    return uuid_value


def uuid_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    try:
        if len(text) == 32 and "-" not in text:
            return str(UUID(hex=text))
        return str(UUID(text))
    except ValueError:
        return text


def _validate_identifier(value: str) -> None:
    if not IDENTIFIER_RE.match(value):
        raise SmokeCameraSetupError("smoke_camera_not_found", {"reason": "unsafe_schema_identifier", "schema": value})


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (1, "1"):
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "yes", "y", "on"}
    return False


def _nested(source: dict[str, Any], *keys: str) -> Any:
    current: Any = source
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


if __name__ == "__main__":
    raise SystemExit(main())
