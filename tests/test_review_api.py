"""HTTP tests for the /review endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, Job
from app.db.session import get_session
from app.main import app


@pytest.fixture
def override_session(db_session: AsyncSession):
    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.clear()


async def _seed_review_entities(
    session: AsyncSession,
) -> tuple[Job, Entity, Entity, Entity]:
    job = Job(query_raw="restaurants in Paris", limit=10, budget_cap_usd=5.0, status="succeeded")
    session.add(job)
    await session.flush()
    low = Entity(
        job_id=job.id,
        name="Low",
        domain="low.example.fr",
        quality_score=40,
        review_status="review",
        field_sources={},
        external_ids={},
    )
    mid = Entity(
        job_id=job.id,
        name="Mid",
        domain="mid.example.fr",
        quality_score=60,
        review_status="review",
        field_sources={},
        external_ids={},
    )
    high = Entity(
        job_id=job.id,
        name="High",
        domain="high.example.fr",
        quality_score=95,
        review_status="approved",
        field_sources={},
        external_ids={},
    )
    session.add_all([low, mid, high])
    await session.flush()
    return job, low, mid, high


@pytest.mark.asyncio
async def test_list_review_returns_only_review_status(
    db_session: AsyncSession, override_session
) -> None:
    job, low, mid, _high = await _seed_review_entities(db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/review?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    names = {item["name"] for item in body["items"]}
    assert names == {"Low", "Mid"}
    assert body["total"] == 2
    # Lowest score first
    assert body["items"][0]["name"] == "Low"
    assert body["items"][0]["job_query"] == "restaurants in Paris"


@pytest.mark.asyncio
async def test_list_review_filter_by_job(db_session: AsyncSession, override_session) -> None:
    job, _low, _mid, _high = await _seed_review_entities(db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/review?job_id={job.id}")
    body = resp.json()
    assert all(item["job_id"] == str(job.id) for item in body["items"])


@pytest.mark.asyncio
async def test_approve_entity_updates_status(db_session: AsyncSession, override_session) -> None:
    _job, low, _mid, _high = await _seed_review_entities(db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/review/{low.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["review_status"] == "approved"

    await db_session.refresh(low)
    assert low.review_status == "approved"


@pytest.mark.asyncio
async def test_reject_entity_updates_status(db_session: AsyncSession, override_session) -> None:
    _job, _low, mid, _high = await _seed_review_entities(db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/review/{mid.id}/reject")
    assert resp.json()["review_status"] == "rejected"


@pytest.mark.asyncio
async def test_approve_unknown_entity_is_404(db_session: AsyncSession, override_session) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/review/{uuid4()}/approve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_csv_export_excludes_rejected_by_default(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(query_raw="q", limit=10, budget_cap_usd=5.0, status="succeeded")
    db_session.add(job)
    await db_session.flush()
    kept = Entity(
        job_id=job.id,
        name="Kept",
        domain="kept.example.com",
        field_sources={},
        external_ids={},
        review_status="approved",
    )
    dropped = Entity(
        job_id=job.id,
        name="Dropped",
        domain="dropped.example.com",
        field_sources={},
        external_ids={},
        review_status="rejected",
    )
    db_session.add_all([kept, dropped])
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/export.csv")
        assert "Kept" in resp.text
        assert "Dropped" not in resp.text

        resp_all = await client.get(f"/jobs/{job.id}/export.csv?include_rejected=true")
        assert "Kept" in resp_all.text
        assert "Dropped" in resp_all.text
