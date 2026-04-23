"""Tests for the aged-record reverification pass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, Job
from app.services.reverify import reverify_stale_entities


async def _seed_job(session: AsyncSession) -> Job:
    job = Job(query_raw="q", limit=1, budget_cap_usd=1.0, status="succeeded")
    session.add(job)
    await session.flush()
    return job


_DOMAIN_COUNTER = [0]


async def _seed_entity(
    session: AsyncSession,
    job: Job,
    *,
    updated_at: datetime,
    email: str | None = None,
    website: str | None = None,
    phone: str | None = None,
    country: str | None = "GB",
    field_sources: dict | None = None,
) -> Entity:
    # Each entity gets a unique domain — the (job_id, domain) unique
    # constraint would otherwise reject a second seed on the same job.
    _DOMAIN_COUNTER[0] += 1
    domain = f"stale-{_DOMAIN_COUNTER[0]}.example"
    e = Entity(
        job_id=job.id,
        name="Stale Co",
        domain=domain,
        website=website,
        email=email,
        phone=phone,
        country=country,
        # Pass field_sources at insert time. Mutating it after with a
        # subsequent flush would trigger TimestampMixin's onupdate=now()
        # and silently un-stale the row.
        field_sources=field_sources or {},
        updated_at=updated_at,
    )
    session.add(e)
    await session.flush()
    return e


@pytest.mark.asyncio
async def test_reverify_skips_fresh_entities(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    fresh_cutoff = datetime.now(UTC) - timedelta(days=5)
    await _seed_entity(db_session, job, updated_at=fresh_cutoff)

    async with httpx.AsyncClient() as http:
        result = await reverify_stale_entities(db_session, http, max_age_days=90, limit=10)
    assert result.scanned == 0


@pytest.mark.asyncio
@respx.mock
async def test_reverify_flags_dead_website(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    stale = datetime.now(UTC) - timedelta(days=120)
    entity = await _seed_entity(
        db_session,
        job,
        updated_at=stale,
        website="https://dead.example",
        field_sources={
            "website": {
                "source": "google_places",
                "fetched_at": "2025-01-01T00:00:00+00:00",
                "confidence": 0.99,
                "liveness": {"status": "alive", "http_status": 200},
            }
        },
    )

    respx.head("https://dead.example").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        result = await reverify_stale_entities(
            db_session, http, max_age_days=90, limit=10
        )

    assert result.scanned == 1
    assert result.websites_checked == 1
    assert result.websites_dead == 1

    await db_session.refresh(entity)
    src = entity.field_sources["website"]
    assert src["liveness"]["status"] == "dead"
    # Confidence dropped after replacing the old "alive" boost (1.02) with
    # "dead" (0.3); exact value depends on the base but it must be lower.
    assert src["confidence"] < 0.5


@pytest.mark.asyncio
@respx.mock
async def test_reverify_repeated_runs_dont_compound_confidence(
    db_session: AsyncSession,
) -> None:
    job = await _seed_job(db_session)
    stale = datetime.now(UTC) - timedelta(days=120)
    entity = await _seed_entity(
        db_session,
        job,
        updated_at=stale,
        website="https://alive.example",
        field_sources={
            "website": {
                "source": "google_places",
                "fetched_at": "2025-01-01T00:00:00+00:00",
                "confidence": 0.99,
                "liveness": {"status": "alive", "http_status": 200},
            }
        },
    )

    respx.head("https://alive.example").mock(return_value=httpx.Response(200))

    async with httpx.AsyncClient() as http:
        await reverify_stale_entities(db_session, http, max_age_days=90, limit=10)
        # Force the entity back to stale so the second pass picks it up.
        entity.updated_at = stale
        await db_session.flush()
        await reverify_stale_entities(db_session, http, max_age_days=90, limit=10)

    await db_session.refresh(entity)
    # Two "alive" passes must not have lifted the confidence above the cap.
    assert entity.field_sources["website"]["confidence"] <= 1.0
    # And should still be roughly where a single pass leaves it.
    assert entity.field_sources["website"]["confidence"] >= 0.98


@pytest.mark.asyncio
async def test_reverify_handles_bad_email(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    stale = datetime.now(UTC) - timedelta(days=120)
    entity = await _seed_entity(
        db_session,
        job,
        updated_at=stale,
        email="not-an-email",
        field_sources={
            "email": {
                "source": "crawler",
                "fetched_at": "2025-01-01T00:00:00+00:00",
                "confidence": 0.9,
                "verification": {"status": "valid", "mx_host": "mx.example"},
            }
        },
    )

    async with httpx.AsyncClient() as http:
        result = await reverify_stale_entities(
            db_session, http, max_age_days=90, limit=10
        )
    assert result.emails_checked == 1
    assert result.emails_invalid == 1

    await db_session.refresh(entity)
    assert entity.field_sources["email"]["verification"]["status"] == "invalid_syntax"
    assert entity.field_sources["email"]["confidence"] == 0.0


@pytest.mark.asyncio
async def test_reverify_skips_duplicate_entities(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    stale = datetime.now(UTC) - timedelta(days=120)
    winner = await _seed_entity(db_session, job, updated_at=stale)
    dupe = await _seed_entity(db_session, job, updated_at=stale)
    dupe.duplicate_of = winner.id
    await db_session.flush()

    async with httpx.AsyncClient() as http:
        result = await reverify_stale_entities(
            db_session, http, max_age_days=90, limit=10
        )
    # Only the winner should count — duplicates are ignored.
    assert result.scanned == 1


@pytest.mark.asyncio
async def test_reverify_respects_limit(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    stale = datetime.now(UTC) - timedelta(days=120)
    for _ in range(5):
        await _seed_entity(db_session, job, updated_at=stale)

    async with httpx.AsyncClient() as http:
        result = await reverify_stale_entities(
            db_session, http, max_age_days=90, limit=2
        )
    assert result.scanned == 2

    remaining_stale = (
        await db_session.execute(
            select(Entity).where(Entity.updated_at < datetime.now(UTC) - timedelta(days=90))
        )
    ).scalars().all()
    assert len(remaining_stale) == 3
