from functools import lru_cache
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    prometheus_url: str
    grafana_url: str
    grafana_service_account_token: str

    # Proxmox VE API (optional — empty string means not configured)
    proxmox_url: str = ""
    proxmox_api_token: str = ""
    proxmox_verify_ssl: bool = False
    proxmox_ca_cert: str = ""
    proxmox_node: str = "proxmox"

    # Extra document directories for RAG ingestion (comma-separated absolute paths)
    extra_docs_dirs: str = ""

    # Proxmox Backup Server API (optional — empty string means not configured)
    pbs_url: str = ""
    pbs_api_token: str = ""
    pbs_verify_ssl: bool = False
    pbs_ca_cert: str = ""
    pbs_node: str = "localhost"
    pbs_default_datastore: str = ""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Lazily load and cache settings. Fails at first call, not at import time."""
    return Settings()  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue] — fields loaded from env
