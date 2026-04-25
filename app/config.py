from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "vigilante-api"
    app_env: str = "local"
    log_level: str = "INFO"

    db_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "vigilante_api"
    db_user: str = "julio"
    db_password: str = ""
    db_schema_api: str = "api"

    default_source_component: str = "vigilante-recognition"
    workflow_source_component: str = "vigilante-api"
    default_query_limit: int = 50
    max_query_limit: int = 200

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        password = self.db_password or ""
        return f"postgresql+psycopg://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def api_schema(self) -> str | None:
        return self.db_schema_api or None

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
