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
        default="postgresql+asyncpg://leadgen:leadgen@127.0.0.1:55432/leadgen",
        description="Async SQLAlchemy DSN for Postgres 16.",
    )
    redis_url: str = "redis://127.0.0.1:6380/0"

    google_places_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    hunter_api_key: str | None = None
    serper_api_key: str | None = None
    # OpenCorporates is accessible anonymously with a lower rate limit; a key
    # unlocks the higher tier. See app/services/opencorporates.py.
    opencorporates_api_key: str | None = None
    # Yelp Fusion API key (Authorization: Bearer). Enables the YelpAdapter
    # fallback in the discovery router; see app/services/yelp.py.
    yelp_api_key: str | None = None
    # Foursquare Places v3 API key. Enables the FoursquareAdapter fallback.
    foursquare_api_key: str | None = None

    sentry_dsn: str | None = None

    default_user_agent: str = "AI-LeadGen-OS/0.1 (+https://combosoft.co.uk/bot)"
    per_domain_min_interval_seconds: float = 2.0
    job_budget_cap_usd: float = 5.0

    # Origins permitted to hit the API in a browser. Local Next.js dev by
    # default; add the deployed dashboard origin before going to prod.
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
    )

    jurisdiction: Literal["EU", "UK", "US"] = "EU"

    # Compliant Mode: when True, the pipeline restricts itself to Tier-1
    # official APIs with clear ToS (Google Places, OpenCorporates) and
    # skips the crawler entirely. Email/phone enrichment from websites is
    # lost, but data minimization is guaranteed. See compliance.md §9.
    compliant_mode: bool = False

    # Column-level encryption for the most sensitive PII (phone, address).
    # Generate a Fernet key with `python -c "from cryptography.fernet import
    # Fernet; print(Fernet.generate_key().decode())"`. When unset, write
    # attempts to encrypted columns raise; reads of pre-existing plaintext
    # still work. Disk-level encryption on the DB host is the primary
    # control — this is defense-in-depth for backups and replicas.
    app_encryption_key: str | None = None

    # OpenTelemetry export. Set OTEL_EXPORTER_OTLP_ENDPOINT to a Grafana
    # Cloud / Honeycomb / self-hosted collector URL (e.g.
    # https://otlp-gateway-prod-eu-west-0.grafana.net/otlp). Additional
    # standard env vars (OTEL_EXPORTER_OTLP_HEADERS, OTEL_SERVICE_NAME)
    # are honored automatically by the OTel SDK — we only read the endpoint
    # to decide whether to install instrumentation at all.
    otel_exporter_otlp_endpoint: str | None = None

    # Auth. JWT_SECRET MUST be set in prod; dev fallback is insecure on purpose
    # so a misconfigured prod deploy fails loudly rather than using a known key.
    jwt_secret: str = "dev-insecure-change-me"  # noqa: S105 — dev placeholder
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days


@lru_cache
def get_settings() -> Settings:
    return Settings()
