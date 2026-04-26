from __future__ import annotations

import os
from uuid import UUID

from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.services.auth_service import hash_password


ORG_1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
ORG_2 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2")
SITE_1 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1")
SITE_2 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2")
ANALYST_USER_ID = UUID("00000000-0000-0000-0000-000000000101")
SUPERVISOR_USER_ID = UUID("00000000-0000-0000-0000-000000000102")


def main() -> None:
    settings = get_settings()
    if settings.is_sqlite:
        raise RuntimeError("scripts/seed_demo_auth.py is intended for the installed PostgreSQL database")
    api_schema = settings.api_schema or "api"
    auth_schema = settings.auth_schema or "auth"
    password = os.getenv("DEMO_AUTH_PASSWORD", "demo123")
    analyst_hash = hash_password(password)
    supervisor_hash = hash_password(password)

    with get_engine().begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO {api_schema}.organization(
                    organization_id, organization_code, name, organization_type, status, timezone
                )
                VALUES
                    (:org1, 'DEMO-ORG-1', 'Demo Organization 1', 'demo', 'active', 'America/Santiago'),
                    (:org2, 'DEMO-ORG-2', 'Demo Organization 2', 'demo', 'active', 'America/Santiago')
                ON CONFLICT (organization_id) DO NOTHING;

                INSERT INTO {api_schema}.site(
                    site_id, site_code, name, timezone, organization_id, site_type, status
                )
                VALUES
                    (:site1, 'DEMO-SITE-1', 'Demo Site 1', 'America/Santiago', :org1, 'branch', 'active'),
                    (:site2, 'DEMO-SITE-2', 'Demo Site 2', 'America/Santiago', :org2, 'branch', 'active')
                ON CONFLICT (site_id) DO UPDATE
                SET organization_id = EXCLUDED.organization_id,
                    updated_at = now();

                INSERT INTO {auth_schema}.role(role_key, display_name, description, is_system)
                VALUES
                    ('analyst', 'Analyst', 'Operación diaria de casos dentro de scope', TRUE),
                    ('supervisor', 'Supervisor', 'Operación avanzada y reasignación dentro de scope', TRUE)
                ON CONFLICT (role_key) DO NOTHING;

                INSERT INTO {auth_schema}.app_user(
                    user_id, email, password_hash, display_name, status, is_active, metadata
                )
                VALUES
                    (:analyst_user_id, 'julio@example.test', :analyst_hash, 'Julio Analyst',
                     'active', TRUE, '{{"username":"julio"}}'::jsonb),
                    (:supervisor_user_id, 'maria@example.test', :supervisor_hash, 'Maria Supervisor',
                     'active', TRUE, '{{"username":"maria"}}'::jsonb)
                ON CONFLICT (user_id) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    status = 'active',
                    is_active = TRUE,
                    updated_at = now();

                INSERT INTO {auth_schema}.user_role(user_id, role_id)
                SELECT :analyst_user_id, role_id FROM {auth_schema}.role WHERE role_key = 'analyst'
                ON CONFLICT (user_id, role_id) DO NOTHING;

                INSERT INTO {auth_schema}.user_role(user_id, role_id)
                SELECT :supervisor_user_id, role_id FROM {auth_schema}.role WHERE role_key = 'supervisor'
                ON CONFLICT (user_id, role_id) DO NOTHING;

                INSERT INTO {auth_schema}.user_organization_scope(
                    user_id, organization_id, scope_role, can_view, can_operate, can_admin, metadata
                )
                VALUES
                    (:analyst_user_id, :org1, 'operator', TRUE, TRUE, FALSE,
                     jsonb_build_object('site_ids', jsonb_build_array(:site1_text))),
                    (:supervisor_user_id, :org1, 'admin', TRUE, TRUE, TRUE,
                     jsonb_build_object('site_ids', jsonb_build_array(:site1_text))),
                    (:supervisor_user_id, :org2, 'admin', TRUE, TRUE, TRUE,
                     jsonb_build_object('site_ids', jsonb_build_array(:site2_text)))
                ON CONFLICT (user_id, organization_id) DO UPDATE
                SET can_view = EXCLUDED.can_view,
                    can_operate = EXCLUDED.can_operate,
                    can_admin = EXCLUDED.can_admin,
                    metadata = EXCLUDED.metadata;
                """
            ),
            {
                "org1": ORG_1,
                "org2": ORG_2,
                "site1": SITE_1,
                "site2": SITE_2,
                "site1_text": str(SITE_1),
                "site2_text": str(SITE_2),
                "analyst_user_id": ANALYST_USER_ID,
                "supervisor_user_id": SUPERVISOR_USER_ID,
                "analyst_hash": analyst_hash,
                "supervisor_hash": supervisor_hash,
            },
        )
    print("Seeded local demo auth users: julio / maria")


if __name__ == "__main__":
    main()
