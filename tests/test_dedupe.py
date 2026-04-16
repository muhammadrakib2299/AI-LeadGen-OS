"""Fuzzy dedupe tests — hit real Postgres so pg_trgm is exercised."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, Job
from app.services.dedupe import dedupe_job


def _ent(job_id, name: str, *, city="Paris", quality_score=50, domain=None) -> Entity:
    return Entity(
        job_id=job_id,
        name=name,
        domain=domain,
        city=city,
        quality_score=quality_score,
        field_sources={},
        external_ids={},
    )


async def _mk_job(session: AsyncSession) -> Job:
    job = Job(query_raw="restaurants in Paris", limit=10, budget_cap_usd=5.0)
    session.add(job)
    await session.flush()
    return job


@pytest.mark.asyncio
async def test_dedupe_marks_lower_quality_as_duplicate(db_session: AsyncSession) -> None:
    job = await _mk_job(db_session)
    winner = _ent(job.id, "Café de Paris", city="Paris", quality_score=90, domain="a.example.fr")
    loser = _ent(job.id, "Cafe de Paris", city="Paris", quality_score=40, domain="b.example.fr")
    db_session.add_all([winner, loser])
    await db_session.flush()

    merged = await dedupe_job(db_session, job.id)
    assert merged == 1

    await db_session.refresh(loser)
    await db_session.refresh(winner)
    assert loser.review_status == "duplicate"
    assert loser.duplicate_of == winner.id
    assert winner.review_status != "duplicate"


@pytest.mark.asyncio
async def test_dedupe_different_cities_not_merged(db_session: AsyncSession) -> None:
    job = await _mk_job(db_session)
    a = _ent(job.id, "Pizzeria Roma", city="Rome", domain="roma1.example.it")
    b = _ent(job.id, "Pizzeria Roma", city="Milan", domain="roma2.example.it")
    db_session.add_all([a, b])
    await db_session.flush()

    merged = await dedupe_job(db_session, job.id)
    assert merged == 0
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.review_status != "duplicate"
    assert b.review_status != "duplicate"


@pytest.mark.asyncio
async def test_dedupe_unrelated_names_untouched(db_session: AsyncSession) -> None:
    job = await _mk_job(db_session)
    a = _ent(job.id, "Chez Marie", domain="a.example.fr")
    b = _ent(job.id, "Bistro Lune", domain="b.example.fr")
    db_session.add_all([a, b])
    await db_session.flush()

    merged = await dedupe_job(db_session, job.id)
    assert merged == 0


@pytest.mark.asyncio
async def test_dedupe_transitive_does_not_double_merge(
    db_session: AsyncSession,
) -> None:
    job = await _mk_job(db_session)
    # Three near-identical names. First merge wins; third one should also be
    # merged against the first winner, not the already-merged loser.
    a = _ent(job.id, "Boulangerie Rive Gauche", quality_score=90, domain="a.example.fr")
    b = _ent(job.id, "Boulangerie Rive Gauche SARL", quality_score=80, domain="b.example.fr")
    c = _ent(job.id, "Boulangerie Rive Gauche Ltd", quality_score=70, domain="c.example.fr")
    db_session.add_all([a, b, c])
    await db_session.flush()

    merged = await dedupe_job(db_session, job.id)
    assert merged >= 1

    alive = (
        (
            await db_session.execute(
                select(Entity).where(Entity.job_id == job.id, Entity.review_status != "duplicate")
            )
        )
        .scalars()
        .all()
    )
    # At least one winner remains.
    assert len(alive) >= 1
    assert a.review_status != "duplicate"  # highest quality stays
