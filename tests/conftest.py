from __future__ import annotations

import os
from pathlib import Path

import pytest


TEST_DB_PATH = Path(__file__).resolve().parent / ".test_vigilante_api.sqlite3"

os.environ["DB_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH}"
os.environ["DB_SCHEMA_API"] = ""
os.environ["DB_SCHEMA_AUTH"] = ""
os.environ["APP_ENV"] = "test"
os.environ["DEFAULT_QUERY_LIMIT"] = "50"
os.environ["MAX_QUERY_LIMIT"] = "200"
os.environ["AUTH_TOKEN_SECRET"] = "test-vigilante-api-token-secret"
os.environ["AUTH_PASSWORD_PBKDF2_ITERATIONS"] = "1000"
os.environ["CAMERA_SECRET_FERNET_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

from app.config import reset_settings_cache
from app.db import Base, get_engine, get_session, reset_db_caches


ORG_1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"
ORG_2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"
SITE_1 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1"
SITE_2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2"

ANALYST_USER_ID = "00000000-0000-0000-0000-000000000101"
SUPERVISOR_USER_ID = "00000000-0000-0000-0000-000000000102"
OUT_OF_SCOPE_USER_ID = "00000000-0000-0000-0000-000000000103"
AUDITOR_USER_ID = "00000000-0000-0000-0000-000000000104"


@pytest.fixture(autouse=True)
def reset_test_database():
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    reset_settings_cache()
    reset_db_caches()
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    seed_auth_fixture()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    reset_db_caches()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


def seed_auth_fixture() -> None:
    from uuid import UUID

    from app.models import AppUser, Organization, Role, Site, UserOrganizationScope, UserRole
    from app.services.auth_service import hash_password

    analyst_role_id = UUID("10000000-0000-0000-0000-000000000001")
    supervisor_role_id = UUID("10000000-0000-0000-0000-000000000002")
    auditor_role_id = UUID("10000000-0000-0000-0000-000000000003")

    with get_session() as session:
        session.add_all(
            [
                Organization(organization_id=UUID(ORG_1)),
                Organization(organization_id=UUID(ORG_2)),
                Site(site_id=UUID(SITE_1), organization_id=UUID(ORG_1)),
                Site(site_id=UUID(SITE_2), organization_id=UUID(ORG_2)),
                Role(role_id=analyst_role_id, role_key="analyst", display_name="Analyst", is_system=True),
                Role(role_id=supervisor_role_id, role_key="supervisor", display_name="Supervisor", is_system=True),
                Role(role_id=auditor_role_id, role_key="auditor", display_name="Auditor", is_system=True),
            ]
        )
        users = [
            (UUID(ANALYST_USER_ID), "julio@example.test", "julio", "Julio Analyst", analyst_role_id, UUID(ORG_1), UUID(SITE_1), True),
            (
                UUID(SUPERVISOR_USER_ID),
                "maria@example.test",
                "maria",
                "Maria Supervisor",
                supervisor_role_id,
                UUID(ORG_1),
                UUID(SITE_1),
                True,
            ),
            (
                UUID(OUT_OF_SCOPE_USER_ID),
                "ana@example.test",
                "ana",
                "Ana Analyst",
                analyst_role_id,
                UUID(ORG_2),
                UUID(SITE_2),
                True,
            ),
            (
                UUID(AUDITOR_USER_ID),
                "auditor@example.test",
                "auditor",
                "Read Only Auditor",
                auditor_role_id,
                UUID(ORG_1),
                UUID(SITE_1),
                False,
            ),
        ]
        for user_id, email, username, display_name, role_id, org_id, site_id, can_operate in users:
            session.add(
                AppUser(
                    user_id=user_id,
                    email=email,
                    password_hash=hash_password("demo123"),
                    display_name=display_name,
                    status="active",
                    is_active=True,
                    user_metadata={"username": username},
                )
            )
            session.add(UserRole(user_id=user_id, role_id=role_id))
            session.add(
                UserOrganizationScope(
                    user_id=user_id,
                    organization_id=org_id,
                    scope_role="admin" if role_id == supervisor_role_id else ("auditor" if role_id == auditor_role_id else "operator"),
                    can_view=True,
                    can_operate=can_operate,
                    can_admin=role_id == supervisor_role_id,
                    scope_metadata={"site_ids": [str(site_id)]},
                )
            )

        session.add(
            UserOrganizationScope(
                user_id=UUID(SUPERVISOR_USER_ID),
                organization_id=UUID(ORG_2),
                scope_role="admin",
                can_view=True,
                can_operate=True,
                can_admin=True,
                scope_metadata={"site_ids": [SITE_2]},
            )
        )
        session.commit()


@pytest.fixture
def auth_headers():
    from fastapi.testclient import TestClient

    from app.main import app

    def _login(username: str = "julio", password: str = "demo123") -> dict[str, str]:
        response = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login
