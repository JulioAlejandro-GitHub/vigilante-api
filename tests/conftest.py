from __future__ import annotations

import os
from pathlib import Path

import pytest


TEST_DB_PATH = Path(__file__).resolve().parent / ".test_vigilante_api.sqlite3"

os.environ["DB_URL"] = f"sqlite+pysqlite:///{TEST_DB_PATH}"
os.environ["DB_SCHEMA_API"] = ""
os.environ["APP_ENV"] = "test"
os.environ["DEFAULT_QUERY_LIMIT"] = "50"
os.environ["MAX_QUERY_LIMIT"] = "200"

from app.config import reset_settings_cache
from app.db import Base, get_engine, reset_db_caches


@pytest.fixture(autouse=True)
def reset_test_database():
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    reset_settings_cache()
    reset_db_caches()
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    reset_db_caches()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
