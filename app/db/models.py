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

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    query_raw: Mapped[str] = mapped_column(Text, nullable=False)
    query_validated: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    budget_cap_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=5.0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0.0)

    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    entities: Mapped[list[Entity]] = relationship(back_populates="job", cascade="all")
    fetches: Mapped[list[RawFetch]] = relationship(back_populates="job", cascade="all")
    exports: Mapped[list[Export]] = relationship(back_populates="job", cascade="all")


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
    phone: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(128), index=True)
    country: Mapped[str | None] = mapped_column(String(2), index=True)  # ISO 3166-1 alpha-2

    category: Mapped[str | None] = mapped_column(String(128))
    socials: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    quality_score: Mapped[int | None] = mapped_column(Integer, index=True)
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )

    # Provenance: per-field {"email": {"source": "crawler", "fetched_at": "...", "confidence": 0.9}}
    field_sources: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    # External IDs across sources (e.g. {"google_place_id": "ChIJ...", "opencorporates_id": "..."})
    external_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

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


class Blacklist(Base, UUIDPKMixin, TimestampMixin):
    """GDPR erasure / opt-out requests. Checked before every entity write.

    Permanent — never deleted. See compliance.md §5.
    """

    __tablename__ = "blacklist"

    email: Mapped[str | None] = mapped_column(String(320), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("email", name="uq_blacklist_email"),
        UniqueConstraint("domain", name="uq_blacklist_domain"),
    )
