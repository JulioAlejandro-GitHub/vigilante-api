from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.db import get_engine
from app.services.auth_service import hash_password, verify_password


ORG_1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
ORG_2 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2")
SITE_1 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1")
SITE_2 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2")
ANALYST_USER_ID = UUID("00000000-0000-0000-0000-000000000101")
SUPERVISOR_USER_ID = UUID("00000000-0000-0000-0000-000000000102")

LOCAL_ENVIRONMENTS = {"local", "dev", "development", "demo", "test"}
DEMO_SEED_MARKER = "local_demo_auth"
MANAGED_ROLE_KEYS = ("admin", "analyst", "auditor", "operator", "reviewer", "supervisor")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class DemoOrganization:
    organization_id: UUID
    organization_code: str
    name: str


@dataclass(frozen=True)
class DemoSite:
    site_id: UUID
    site_code: str
    name: str
    organization_id: UUID


@dataclass(frozen=True)
class DemoScope:
    organization_id: UUID
    site_ids: tuple[UUID, ...]
    scope_role: str
    can_operate: bool
    can_admin: bool


@dataclass(frozen=True)
class DemoUser:
    requested_user_id: UUID
    username: str
    email: str
    display_name: str
    role_key: str
    scopes: tuple[DemoScope, ...]


DEMO_ORGANIZATIONS = (
    DemoOrganization(ORG_1, "DEMO-ORG-1", "Demo Organization 1"),
    DemoOrganization(ORG_2, "DEMO-ORG-2", "Demo Organization 2"),
)

DEMO_SITES = (
    DemoSite(SITE_1, "DEMO-SITE-1", "Demo Site 1", ORG_1),
    DemoSite(SITE_2, "DEMO-SITE-2", "Demo Site 2", ORG_2),
)

DEMO_USERS = (
    DemoUser(
        requested_user_id=ANALYST_USER_ID,
        username="julio",
        email="julio@example.test",
        display_name="Julio Analyst",
        role_key="analyst",
        scopes=(DemoScope(ORG_1, (SITE_1,), "operator", True, False),),
    ),
    DemoUser(
        requested_user_id=SUPERVISOR_USER_ID,
        username="maria",
        email="maria@example.test",
        display_name="Maria Supervisor",
        role_key="supervisor",
        scopes=(
            DemoScope(ORG_1, (SITE_1,), "admin", True, True),
            DemoScope(ORG_2, (SITE_2,), "admin", True, True),
        ),
    ),
)

ROLE_DEFINITIONS = {
    "analyst": ("Analyst", "Operación diaria de casos dentro de scope"),
    "supervisor": ("Supervisor", "Operación avanzada y reasignación dentro de scope"),
}

REQUIRED_COLUMNS = {
    "api": {
        "organization": {
            "organization_id",
            "organization_code",
            "name",
            "organization_type",
            "status",
            "timezone",
            "metadata",
            "updated_at",
        },
        "site": {
            "site_id",
            "site_code",
            "name",
            "timezone",
            "organization_id",
            "site_type",
            "status",
            "metadata",
            "updated_at",
        },
    },
    "auth": {
        "app_user": {
            "user_id",
            "email",
            "password_hash",
            "display_name",
            "status",
            "is_active",
            "metadata",
            "updated_at",
        },
        "role": {"role_id", "role_key", "display_name", "description", "is_system", "updated_at"},
        "user_role": {"user_id", "role_id"},
        "user_organization_scope": {
            "user_id",
            "organization_id",
            "scope_role",
            "can_view",
            "can_operate",
            "can_admin",
            "metadata",
        },
    },
}


def main() -> None:
    settings = get_settings()
    _ensure_local_postgresql(settings)

    api_schema_name = settings.api_schema or "api"
    auth_schema_name = settings.auth_schema or "auth"
    api_schema = _quote_identifier(api_schema_name)
    auth_schema = _quote_identifier(auth_schema_name)
    password = os.getenv("DEMO_AUTH_PASSWORD", "demo123")

    with get_engine().begin() as connection:
        _assert_required_schema(connection, api_schema_name, auth_schema_name)
        for organization in DEMO_ORGANIZATIONS:
            _upsert_organization(connection, api_schema, organization)
        for site in DEMO_SITES:
            _upsert_site(connection, api_schema, site)

        role_ids = {
            role_key: _upsert_role(connection, auth_schema, role_key, display_name, description)
            for role_key, (display_name, description) in ROLE_DEFINITIONS.items()
        }
        for user in DEMO_USERS:
            user_id = _upsert_demo_user(connection, auth_schema, user, password)
            _reset_managed_roles(connection, auth_schema, user_id, role_ids[user.role_key])
            expected_org_ids = tuple(scope.organization_id for scope in user.scopes)
            _delete_obsolete_demo_scopes(connection, auth_schema, user_id, expected_org_ids)
            for scope in user.scopes:
                _upsert_scope(connection, auth_schema, user_id, scope)

    print("Seeded local demo auth users: julio, maria")


def _ensure_local_postgresql(settings: Any) -> None:
    if settings.is_sqlite:
        raise RuntimeError("scripts/seed_demo_auth.py is intended for the installed PostgreSQL database")
    if settings.app_env.lower() not in LOCAL_ENVIRONMENTS:
        raise RuntimeError(
            "scripts/seed_demo_auth.py is a local/demo seed only. "
            "Run it with APP_ENV=local, APP_ENV=dev or APP_ENV=demo."
        )


def _assert_required_schema(connection: Connection, api_schema_name: str, auth_schema_name: str) -> None:
    schema_names = {"api": api_schema_name, "auth": auth_schema_name}
    missing: list[str] = []
    for logical_schema, tables in REQUIRED_COLUMNS.items():
        schema_name = schema_names[logical_schema]
        for table_name, required_columns in tables.items():
            rows = connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = :schema_name
                      AND table_name = :table_name
                    """
                ),
                {"schema_name": schema_name, "table_name": table_name},
            ).scalars()
            found_columns = {str(column_name) for column_name in rows}
            for column_name in sorted(required_columns - found_columns):
                missing.append(f"{schema_name}.{table_name}.{column_name}")
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            "The local database does not have the auth/RBAC schema expected by this seed. "
            f"Missing columns: {missing_list}"
        )


def _upsert_organization(connection: Connection, api_schema: str, organization: DemoOrganization) -> None:
    _assert_no_pk_code_conflict(
        connection,
        f"{api_schema}.organization",
        id_column="organization_id",
        code_column="organization_code",
        item_id=organization.organization_id,
        item_code=organization.organization_code,
    )
    connection.execute(
        text(
            f"""
            INSERT INTO {api_schema}.organization(
                organization_id, organization_code, name, organization_type, status, timezone, metadata
            )
            VALUES (
                :organization_id, :organization_code, :name, 'demo', 'active', 'America/Santiago',
                CAST(:metadata AS jsonb)
            )
            ON CONFLICT (organization_id) DO UPDATE
            SET organization_code = EXCLUDED.organization_code,
                name = EXCLUDED.name,
                organization_type = EXCLUDED.organization_type,
                status = EXCLUDED.status,
                timezone = EXCLUDED.timezone,
                metadata = COALESCE({api_schema}.organization.metadata, '{{}}'::jsonb) || EXCLUDED.metadata,
                updated_at = now()
            """
        ),
        {
            "organization_id": organization.organization_id,
            "organization_code": organization.organization_code,
            "name": organization.name,
            "metadata": _json({"seed": DEMO_SEED_MARKER}),
        },
    )


def _upsert_site(connection: Connection, api_schema: str, site: DemoSite) -> None:
    _assert_no_pk_code_conflict(
        connection,
        f"{api_schema}.site",
        id_column="site_id",
        code_column="site_code",
        item_id=site.site_id,
        item_code=site.site_code,
    )
    connection.execute(
        text(
            f"""
            INSERT INTO {api_schema}.site(
                site_id, site_code, name, timezone, organization_id, site_type, status, metadata
            )
            VALUES (
                :site_id, :site_code, :name, 'America/Santiago', :organization_id, 'branch', 'active',
                CAST(:metadata AS jsonb)
            )
            ON CONFLICT (site_id) DO UPDATE
            SET site_code = EXCLUDED.site_code,
                name = EXCLUDED.name,
                timezone = EXCLUDED.timezone,
                organization_id = EXCLUDED.organization_id,
                site_type = EXCLUDED.site_type,
                status = EXCLUDED.status,
                metadata = COALESCE({api_schema}.site.metadata, '{{}}'::jsonb) || EXCLUDED.metadata,
                updated_at = now()
            """
        ),
        {
            "site_id": site.site_id,
            "site_code": site.site_code,
            "name": site.name,
            "organization_id": site.organization_id,
            "metadata": _json({"seed": DEMO_SEED_MARKER}),
        },
    )


def _upsert_role(
    connection: Connection,
    auth_schema: str,
    role_key: str,
    display_name: str,
    description: str,
) -> UUID:
    row = connection.execute(
        text(
            f"""
            INSERT INTO {auth_schema}.role(role_key, display_name, description, is_system)
            VALUES (:role_key, :display_name, :description, TRUE)
            ON CONFLICT (role_key) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                is_system = TRUE,
                updated_at = now()
            RETURNING role_id
            """
        ),
        {"role_key": role_key, "display_name": display_name, "description": description},
    ).one()
    return row.role_id


def _upsert_demo_user(connection: Connection, auth_schema: str, user: DemoUser, password: str) -> UUID:
    existing = _find_existing_demo_user(connection, auth_schema, user)
    user_id = existing["user_id"] if existing else user.requested_user_id
    _assert_username_available(connection, auth_schema, user.username, user_id)

    existing_hash = existing["password_hash"] if existing else None
    password_hash = existing_hash if verify_password(password, existing_hash) else hash_password(password)

    row = connection.execute(
        text(
            f"""
            INSERT INTO {auth_schema}.app_user(
                user_id, email, password_hash, display_name, status, is_active, metadata
            )
            VALUES (
                :user_id, :email, :password_hash, :display_name, 'active', TRUE, CAST(:metadata AS jsonb)
            )
            ON CONFLICT (user_id) DO UPDATE
            SET email = EXCLUDED.email,
                password_hash = EXCLUDED.password_hash,
                display_name = EXCLUDED.display_name,
                status = 'active',
                is_active = TRUE,
                metadata = COALESCE({auth_schema}.app_user.metadata, '{{}}'::jsonb) || EXCLUDED.metadata,
                updated_at = now()
            RETURNING user_id
            """
        ),
        {
            "user_id": user_id,
            "email": user.email,
            "password_hash": password_hash,
            "display_name": user.display_name,
            "metadata": _json({"username": user.username, "seed": DEMO_SEED_MARKER}),
        },
    ).one()
    return row.user_id


def _find_existing_demo_user(connection: Connection, auth_schema: str, user: DemoUser) -> dict[str, Any] | None:
    rows = connection.execute(
        text(
            f"""
            SELECT user_id, email, password_hash
            FROM {auth_schema}.app_user
            WHERE user_id = :user_id
               OR lower(email::text) = lower(:email)
            ORDER BY CASE WHEN user_id = :user_id THEN 0 ELSE 1 END
            """
        ),
        {"user_id": user.requested_user_id, "email": user.email},
    ).mappings().all()
    user_ids = {str(row["user_id"]) for row in rows}
    if len(user_ids) > 1:
        raise RuntimeError(
            f"Refusing to seed demo user {user.username!r}: requested user_id and email match different rows."
        )
    return dict(rows[0]) if rows else None


def _assert_username_available(connection: Connection, auth_schema: str, username: str, user_id: UUID) -> None:
    row = connection.execute(
        text(
            f"""
            SELECT user_id, email
            FROM {auth_schema}.app_user
            WHERE lower(metadata->>'username') = lower(:username)
              AND user_id <> :user_id
            LIMIT 1
            """
        ),
        {"username": username, "user_id": user_id},
    ).mappings().first()
    if row:
        raise RuntimeError(
            f"Refusing to seed demo user {username!r}: metadata.username is already used by "
            f"user_id={row['user_id']} email={row['email']}."
        )


def _reset_managed_roles(connection: Connection, auth_schema: str, user_id: UUID, expected_role_id: UUID) -> None:
    statement = text(
        f"""
        DELETE FROM {auth_schema}.user_role user_role
        USING {auth_schema}.role role
        WHERE user_role.role_id = role.role_id
          AND user_role.user_id = :user_id
          AND lower(role.role_key::text) IN :managed_role_keys
          AND user_role.role_id <> :expected_role_id
        """
    ).bindparams(bindparam("managed_role_keys", expanding=True))
    connection.execute(
        statement,
        {"user_id": user_id, "expected_role_id": expected_role_id, "managed_role_keys": list(MANAGED_ROLE_KEYS)},
    )
    connection.execute(
        text(
            f"""
            INSERT INTO {auth_schema}.user_role(user_id, role_id)
            VALUES (:user_id, :role_id)
            ON CONFLICT (user_id, role_id) DO NOTHING
            """
        ),
        {"user_id": user_id, "role_id": expected_role_id},
    )


def _delete_obsolete_demo_scopes(
    connection: Connection,
    auth_schema: str,
    user_id: UUID,
    expected_org_ids: tuple[UUID, ...],
) -> None:
    statement = text(
        f"""
        DELETE FROM {auth_schema}.user_organization_scope
        WHERE user_id = :user_id
          AND organization_id IN :demo_org_ids
          AND organization_id NOT IN :expected_org_ids
        """
    ).bindparams(
        bindparam("demo_org_ids", expanding=True),
        bindparam("expected_org_ids", expanding=True),
    )
    connection.execute(
        statement,
        {
            "user_id": user_id,
            "demo_org_ids": [ORG_1, ORG_2],
            "expected_org_ids": list(expected_org_ids),
        },
    )


def _upsert_scope(connection: Connection, auth_schema: str, user_id: UUID, scope: DemoScope) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {auth_schema}.user_organization_scope(
                user_id, organization_id, scope_role, can_view, can_operate, can_admin, metadata
            )
            VALUES (
                :user_id, :organization_id, :scope_role, TRUE, :can_operate, :can_admin, CAST(:metadata AS jsonb)
            )
            ON CONFLICT (user_id, organization_id) DO UPDATE
            SET scope_role = EXCLUDED.scope_role,
                can_view = EXCLUDED.can_view,
                can_operate = EXCLUDED.can_operate,
                can_admin = EXCLUDED.can_admin,
                metadata = EXCLUDED.metadata
            """
        ),
        {
            "user_id": user_id,
            "organization_id": scope.organization_id,
            "scope_role": scope.scope_role,
            "can_operate": scope.can_operate,
            "can_admin": scope.can_admin,
            "metadata": _json(
                {
                    "site_ids": [str(site_id) for site_id in scope.site_ids],
                    "seed": DEMO_SEED_MARKER,
                }
            ),
        },
    )


def _assert_no_pk_code_conflict(
    connection: Connection,
    table_name: str,
    *,
    id_column: str,
    code_column: str,
    item_id: UUID,
    item_code: str,
) -> None:
    rows = connection.execute(
        text(
            f"""
            SELECT {id_column} AS item_id, {code_column} AS item_code
            FROM {table_name}
            WHERE {id_column} = :item_id
               OR {code_column} = :item_code
            """
        ),
        {"item_id": item_id, "item_code": item_code},
    ).mappings().all()
    item_id_text = str(item_id)
    for row in rows:
        if str(row["item_code"]) == item_code and str(row["item_id"]) != item_id_text:
            raise RuntimeError(
                f"Refusing to seed {table_name}: code={item_code!r} already belongs to id={row['item_id']}."
            )
    ids = {str(row["item_id"]) for row in rows}
    if len(ids) > 1:
        raise RuntimeError(
            f"Refusing to seed {table_name}: id={item_id} and code={item_code!r} match different rows."
        )


def _quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise RuntimeError(f"Unsafe SQL identifier in seed configuration: {identifier!r}")
    return f'"{identifier}"'


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    main()
