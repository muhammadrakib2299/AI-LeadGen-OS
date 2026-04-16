"""Integration test for scripts/retention_sweep.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, RawFetch
from scripts.retention_sweep import sweep


@pytest.mark.asyncio
async def test_sweep_deletes_old_raw_fetches_only(db_session: AsyncSession) -> None:
    job = Job(query_raw="q", limit=1, budget_cap_usd=1.0, status="succeeded")
    db_session.add(job)
    await db_session.flush()

    now = datetime.now(UTC)
    fresh = RawFetch(
        job_id=job.id,
        source_slug="test",
        url="https://example.com/fresh",
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=10),
    )
    stale = RawFetch(
        job_id=job.id,
        source_slug="test",
        url="https://example.com/stale",
        created_at=now - timedelta(days=120),
        updated_at=now - timedelta(days=120),
    )
    db_session.add_all([fresh, stale])
    await db_session.flush()

    counts = await sweep(db_session, dry_run=False)
    assert counts["raw_fetches"] == 1

    remaining = (
        (await db_session.execute(select(RawFetch).where(RawFetch.job_id == job.id)))
        .scalars()
        .all()
    )
    assert {r.url for r in remaining} == {"https://example.com/fresh"}


@pytest.mark.asyncio
async def test_sweep_dry_run_does_not_delete(db_session: AsyncSession) -> None:
    job = Job(query_raw="q", limit=1, budget_cap_usd=1.0, status="succeeded")
    db_session.add(job)
    await db_session.flush()

    old = datetime.now(UTC) - timedelta(days=365)
    db_session.add(
        RawFetch(
            job_id=job.id,
            source_slug="test",
            url="https://example.com/old",
            created_at=old,
            updated_at=old,
        )
    )
    await db_session.flush()

    counts = await sweep(db_session, dry_run=True)
    assert counts["raw_fetches"] == 1

    remaining = (
        (await db_session.execute(select(RawFetch).where(RawFetch.job_id == job.id)))
        .scalars()
        .all()
    )
    assert len(remaining) == 1
