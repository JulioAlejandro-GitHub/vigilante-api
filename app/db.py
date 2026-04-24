from __future__ import annotations

from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    settings = get_settings()
    kwargs = {"future": True}
    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(settings.database_url, **kwargs)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Session:
    return get_session_factory()()


def session_dependency() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def ping_database() -> None:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


def reset_db_caches() -> None:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
