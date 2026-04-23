"""SQLAlchemy ORM models for Phase 1.

Design notes:
- Provenance per field: `entities.field_sources` JSONB stores
  {field_name: {"source": source_slug, "fetched_at": iso8601, "confidence": 0..1}}
  per compliance.md §7 and overview.md §7 item 1.
- PII (email, phone) stored unencrypted in Phase 1 for speed; pgcrypto column
  encryption is a Phase 2 hardening (compliance.md §8).
- raw_fetches retention = 90d; entity retention = 24mo; see compliance.md §6.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin
from app.db.types import EncryptedString


class Source(Base, UUIDPKMixin, TimestampMixin):
    """Registry of data sources we're allowed to query. Seeded from sources.md."""

    __tablename__ = "sources"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trust_score: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, default=0.8)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)


class Job(Base, UUIDPKMixin, TimestampMixin):
    """A single lead-generation run."""

    __tablename__ = "jobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    # 'discovery' = NL query → Places search; 'bulk_enrichment' = client-supplied list.
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, default="discovery")
    query_raw: Mapped[str] = mapped_column(Text, nullable=False)
    query_validated: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # For bulk_enrichment jobs: client-supplied list of {name?, website?, domain?} rows.
    seed_entities: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    budget_cap_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=5.0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0.0)

    # Live progress counters. Written by the runner so /jobs GET can show a
    # progress bar without the caller polling the entities table.
    places_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    places_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Client-supplied key for safe retries. Nullable: pre-existing clients don't need it.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    entities: Mapped[list[Entity]] = relationship(back_populates="job", cascade="all")
    fetches: Mapped[list[RawFetch]] = relationship(back_populates="job", cascade="all")
    exports: Mapped[list[Export]] = relationship(back_populates="job", cascade="all")

    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),)


class Entity(Base, UUIDPKMixin, TimestampMixin):
    """A discovered business. One row per merged entity across sources."""

    __tablename__ = "entities"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(512), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    website: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    # Encrypted at the column level (app/db/types.py). We keep the
    # declared max length generous enough to fit a Fernet ciphertext of a
    # reasonable plaintext. See app/core/crypto.py for the on-disk format.
    phone: Mapped[str | None] = mapped_column(EncryptedString(255))
    address: Mapped[str | None] = mapped_column(EncryptedString(1024))
    city: Mapped[str | None] = mapped_column(String(128), index=True)
    country: Mapped[str | None] = mapped_column(String(2), index=True)  # ISO 3166-1 alpha-2

    category: Mapped[str | None] = mapped_column(String(128))
    socials: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    quality_score: Mapped[int | None] = mapped_column(Integer, index=True)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    # Sales pipeline state, distinct from review_status (which is quality
    # control). Operator advances this manually as leads move through
    # outreach. Free-form string with a small intended vocabulary so we
    # can add states without a migration.
    lead_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="new", index=True
    )
    lead_status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    lead_notes: Mapped[str | None] = mapped_column(Text)

    # When this row is a fuzzy-match duplicate of another entity, points at the
    # winner. Null for kept / standalone rows. See app/services/dedupe.py.
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Provenance: per-field {"email": {"source": "crawler", "fetched_at": "...", "confidence": 0.9}}
    field_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # External IDs across sources (e.g. {"google_place_id": "ChIJ...", "opencorporates_id": "..."})
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    job: Mapped[Job] = relationship(back_populates="entities")

    __table_args__ = (
        UniqueConstraint("job_id", "domain", name="uq_entity_job_domain"),
        Index("ix_entity_external_ids_gin", "external_ids", postgresql_using="gin"),
    )


class RawFetch(Base, UUIDPKMixin, TimestampMixin):
    """Audit-grade log of every external fetch. Doubles as compliance §7 audit log.

    Retention: 90 days. See scripts/retention_sweep.py (to be added).
    """

    __tablename__ = "raw_fetches"

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        index=True,
    )
    source_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False, default="GET")
    legal_basis: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legitimate_interest"
    )
    response_status: Mapped[int | None] = mapped_column(Integer)
    bytes_fetched: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # Raw JSON or compressed HTML snippet, depending on source
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0.0)

    job: Mapped[Job | None] = relationship(back_populates="fetches")


class Export(Base, UUIDPKMixin, TimestampMixin):
    """A generated export artifact. File lives on disk (or S3 later)."""

    __tablename__ = "exports"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="csv")
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    job: Mapped[Job] = relationship(back_populates="exports")


class SearchTemplate(Base, UUIDPKMixin, TimestampMixin):
    """A saved discovery query. Lets the operator re-run common searches in one click."""

    __tablename__ = "search_templates"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    default_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    default_budget_cap_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, default=5.0
    )

    # Template names are unique per tenant, not globally.
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_search_templates_tenant_name"),
    )


class Tenant(Base, UUIDPKMixin, TimestampMixin):
    """Billing + isolation boundary. Every user belongs to exactly one tenant.

    Phase 5 groundwork: the first user of a tenant is implicitly its owner;
    team-seat invites (additional users joining an existing tenant) are a
    separate block. For now, registration creates a tenant per user.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "free" | "standard" | "past_due" | "canceled". Free is the default
    # for new tenants; webhook updates move the state. No enum type in the
    # DB on purpose — lets us add plans without a migration.
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(128), unique=True)


class User(Base, UUIDPKMixin, TimestampMixin):
    """Operator account. Belongs to exactly one tenant.

    Multi-tenant data scoping across jobs/templates/blacklist lives in a
    follow-up block; until then, this column enables future row-level
    tenant filtering without a schema change.
    """

    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class S3ExportDestination(Base, UUIDPKMixin, TimestampMixin):
    """Tenant's S3 destination for scheduled / on-demand CSV pushes.

    Credentials are stored in EncryptedString — operators should issue an
    IAM user with `s3:PutObject` on a single bucket prefix and rotate keys
    via this table's POST endpoint.
    """

    __tablename__ = "s3_export_destinations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    access_key_id: Mapped[str] = mapped_column(EncryptedString(255), nullable=False)
    secret_access_key: Mapped[str] = mapped_column(EncryptedString(1024), nullable=False)
    last_export_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoogleSheetsDestination(Base, UUIDPKMixin, TimestampMixin):
    """Tenant's Google Sheets destination for on-demand exports.

    `service_account_json` is the full SA key blob (encrypted at rest).
    The tenant must share the target spreadsheet with the service account's
    client_email before exports work — there's no way to bootstrap that
    from our side.
    """

    __tablename__ = "google_sheets_destinations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    service_account_json: Mapped[str] = mapped_column(
        EncryptedString(8192), nullable=False
    )
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False)
    worksheet_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="Leads"
    )
    last_export_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantInvite(Base, UUIDPKMixin, TimestampMixin):
    """Outstanding invitation for someone to join a tenant.

    The token is the only secret; revealed once at create time. Acceptance
    consumes the row (sets accepted_at) and creates a User under the
    tenant. Invites expire after 14 days.
    """

    __tablename__ = "tenant_invites"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HubspotIntegration(Base, UUIDPKMixin, TimestampMixin):
    """Per-tenant HubSpot private-app token.

    Stored in `EncryptedString` so the value is encrypted at rest if
    APP_ENCRYPTION_KEY is configured. One row per tenant — if HubSpot is
    reconnected, we overwrite the same row rather than piling duplicates.
    """

    __tablename__ = "hubspot_integrations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    access_token: Mapped[str] = mapped_column(EncryptedString(1024), nullable=False)
    last_export_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PipedriveIntegration(Base, UUIDPKMixin, TimestampMixin):
    """Per-tenant Pipedrive personal API token.

    `company_domain` is the tenant's Pipedrive subdomain (e.g. "acme" for
    acme.pipedrive.com). Optional: when absent we fall back to the global
    api.pipedrive.com host.
    """

    __tablename__ = "pipedrive_integrations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    api_token: Mapped[str] = mapped_column(EncryptedString(1024), nullable=False)
    company_domain: Mapped[str | None] = mapped_column(String(128))
    last_export_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Webhook(Base, UUIDPKMixin, TimestampMixin):
    """Outbound webhook endpoint configured by a tenant.

    Fires on specific event types (for v1: just 'job.completed'). Payloads
    are signed with HMAC-SHA256 using `secret` so recipients can verify
    origin. Disabled webhooks stop receiving events; they're preserved so
    the UI can still show history.
    """

    __tablename__ = "webhooks"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    # Comma-separated event types the hook listens for.
    events: Mapped[str] = mapped_column(String(255), nullable=False, default="job.completed")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WebhookDelivery(Base, UUIDPKMixin, TimestampMixin):
    """Audit row for one webhook attempt."""

    __tablename__ = "webhook_deliveries"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    # Signed body we POSTed — short-retained for debugging.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ApiKey(Base, UUIDPKMixin, TimestampMixin):
    """Long-lived programmatic credential. Sent via `X-API-Key` header.

    We store SHA-256 of the full key (not bcrypt) because API keys are
    high-entropy (>=128 bits) — offline cracking is infeasible and the hash
    is looked up on every request, so speed matters. `prefix` is the first
    12 chars of the plaintext key, kept un-hashed so the UI can show "which
    key" without revealing the secret.
    """

    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Hard expiry — the auth check rejects keys past this time. Set by
    # `POST /api-keys/{id}/rotate` to give the old key a short overlap
    # window so callers can swap to the new one without downtime. Null
    # means no expiry.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on the OLD key when rotated. Lets the UI/API show "this key was
    # superseded by <id>" rather than just disappearing.
    rotated_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
    )


class Blacklist(Base, UUIDPKMixin, TimestampMixin):
    """GDPR erasure / opt-out requests. Checked before every entity write.

    Permanent — never deleted. See compliance.md §5.
    """

    __tablename__ = "blacklist"

    # GDPR subject requests arriving via /privacy/opt-out have no tenant
    # context — they apply to the tenant whose pipeline surfaced the
    # address. We resolve that at write time: opt-out POSTs for a domain
    # fan out to every tenant that currently holds data for it. See
    # app/services/blacklist.py. For tenant-authored entries the column
    # holds the calling tenant directly.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    reason: Mapped[str | None] = mapped_column(Text)

    # Uniqueness is now scoped per tenant — two tenants can each blacklist
    # the same address independently (an opt-out request to Tenant A
    # shouldn't force-remove a B2B lead from Tenant B's pipeline).
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_blacklist_tenant_email"),
        UniqueConstraint("tenant_id", "domain", name="uq_blacklist_tenant_domain"),
    )
