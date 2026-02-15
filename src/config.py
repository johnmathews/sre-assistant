from functools import lru_cache
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    openai_api_key: str
    prometheus_url: str
    grafana_url: str
    grafana_service_account_token: str

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Lazily load and cache settings. Fails at first call, not at import time."""
    return Settings()  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue] — fields loaded from env
