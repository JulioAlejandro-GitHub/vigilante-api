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
    db_schema_auth: str = "auth"

    default_source_component: str = "vigilante-recognition"
    workflow_source_component: str = "vigilante-api"
    default_query_limit: int = 50
    max_query_limit: int = 200
    auth_token_secret: str | None = None
    auth_token_issuer: str = "vigilante-api"
    auth_token_ttl_minutes: int = 480
    auth_password_pbkdf2_iterations: int = 260000
    camera_secret_fernet_key: str | None = None
    media_service_base_url: str | None = None
    media_service_public_base_url: str | None = None
    media_service_timeout_seconds: float = 2.0
    media_resolution_max_refs: int = 20
    live_event_projection_enabled: bool = True
    live_projection_max_events: int = 200
    live_case_suggestion_projection_enabled: bool = True
    live_case_suggestion_min_events: int = 3
    live_case_suggestion_window_minutes: int = 15
    include_fixture_projections_when_live: bool = False
    recognition_db_url: str | None = None
    recognition_db_host: str | None = None
    recognition_db_port: int | None = None
    recognition_db_name: str = "vigilante_recognition"
    recognition_db_user: str | None = None
    recognition_db_password: str | None = None

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
    def auth_schema(self) -> str | None:
        return self.db_schema_auth or None

    @property
    def token_secret(self) -> str:
        if self.auth_token_secret:
            return self.auth_token_secret
        if self.app_env in {"local", "test"}:
            return "local-dev-only-vigilante-api-token-secret-change-me"
        raise RuntimeError("AUTH_TOKEN_SECRET is required outside local/test environments")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def live_projection_is_enabled(self) -> bool:
        return self.live_event_projection_enabled and self.app_env in {"local", "dev", "development", "demo"}

    @property
    def recognition_database_url(self) -> str:
        if self.recognition_db_url:
            return self.recognition_db_url
        host = self.recognition_db_host or self.db_host
        port = self.recognition_db_port or self.db_port
        user = self.recognition_db_user or self.db_user
        password = self.recognition_db_password if self.recognition_db_password is not None else self.db_password
        return f"postgresql+psycopg://{user}:{password or ''}@{host}:{port}/{self.recognition_db_name}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
