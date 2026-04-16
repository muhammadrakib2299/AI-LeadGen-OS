"""Application settings. Loaded from env / secrets manager via Pydantic."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = Field(
        default="postgresql+asyncpg://leadgen:leadgen@localhost:5433/leadgen",
        description="Async SQLAlchemy DSN for Postgres 16.",
    )
    redis_url: str = "redis://localhost:6379/0"

    google_places_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    hunter_api_key: str | None = None
    serper_api_key: str | None = None

    sentry_dsn: str | None = None

    default_user_agent: str = "AI-LeadGen-OS/0.1 (+https://combosoft.co.uk/bot)"
    per_domain_min_interval_seconds: float = 2.0
    job_budget_cap_usd: float = 5.0

    jurisdiction: Literal["EU", "UK", "US"] = "EU"


@lru_cache
def get_settings() -> Settings:
    return Settings()
