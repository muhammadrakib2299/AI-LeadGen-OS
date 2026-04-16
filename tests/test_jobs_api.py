"""HTTP tests for the /jobs endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, Job
from app.db.session import get_session
from app.main import app


async def _override_dep(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


@pytest.fixture
def override_session(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    # Stop POST /jobs from launching the real pipeline in asyncio.create_task.
    from app.api import jobs as jobs_api

    async def _noop_bg(job_id) -> None:  # type: ignore[no-untyped-def]
        pass

    monkeypatch.setattr(jobs_api, "_run_in_background", _noop_bg)

    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_jobs_creates_pending_job(db_session: AsyncSession, override_session) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs",
            json={"query": "restaurants in Paris", "limit": 50, "budget_cap_usd": 2.0},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["query_raw"] == "restaurants in Paris"
    assert body["limit"] == 50
    assert body["budget_cap_usd"] == 2.0
    assert body["entity_count"] == 0

    job = await db_session.get(Job, body["id"])
    assert job is not None
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_post_jobs_rejects_short_query(db_session: AsyncSession, override_session) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/jobs", json={"query": "x"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_job_returns_details(db_session: AsyncSession, override_session) -> None:
    job = Job(
        query_raw="cafes in Lisbon",
        limit=10,
        budget_cap_usd=1.0,
        status="succeeded",
        cost_usd=0.03,
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add(
        Entity(
            job_id=job.id,
            name="Café Something",
            domain="cafe.example.pt",
            field_sources={},
            external_ids={},
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["entity_count"] == 1
    assert body["query_raw"] == "cafes in Lisbon"


@pytest.mark.asyncio
async def test_get_job_not_found(db_session: AsyncSession, override_session) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_csv_for_completed_job(db_session: AsyncSession, override_session) -> None:
    job = Job(
        query_raw="restaurants in Paris",
        limit=5,
        budget_cap_usd=5.0,
        status="succeeded",
        cost_usd=0.05,
    )
    db_session.add(job)
    await db_session.flush()

    db_session.add(
        Entity(
            job_id=job.id,
            name="Le Petit Bistro",
            domain="lepetitbistro.example.fr",
            website="https://lepetitbistro.example.fr",
            email="contact@lepetitbistro.example.fr",
            phone="+33142000000",
            city="Paris",
            country="FR",
            category="restaurant",
            socials={"linkedin": "https://linkedin.com/company/bistro"},
            field_sources={
                "email": {"source": "crawler", "confidence": 0.9, "fetched_at": "x"},
                "phone": {"source": "crawler", "confidence": 0.9, "fetched_at": "x"},
            },
            external_ids={"google_place_id": "ChIJ_x"},
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert f"leadgen-{job.id}.csv" in resp.headers["content-disposition"]
    text = resp.text
    assert "name,website,email" in text.splitlines()[0]
    assert "Le Petit Bistro" in text
    assert "contact@lepetitbistro.example.fr" in text
    assert "https://linkedin.com/company/bistro" in text
    assert "ChIJ_x" in text
    assert "crawler" in text


@pytest.mark.asyncio
async def test_list_jobs_returns_newest_first_with_entity_counts(
    db_session: AsyncSession, override_session
) -> None:
    from datetime import UTC, datetime, timedelta

    t_old = datetime.now(UTC) - timedelta(hours=1)
    t_new = datetime.now(UTC)
    older = Job(
        query_raw="older",
        limit=10,
        budget_cap_usd=1.0,
        status="succeeded",
        created_at=t_old,
        updated_at=t_old,
    )
    newer = Job(
        query_raw="newer",
        limit=10,
        budget_cap_usd=1.0,
        status="running",
        created_at=t_new,
        updated_at=t_new,
    )
    db_session.add(older)
    db_session.add(newer)
    await db_session.flush()
    db_session.add(
        Entity(
            job_id=older.id,
            name="X",
            domain="x.example.com",
            field_sources={},
            external_ids={},
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert body["limit"] == 10
    assert body["offset"] == 0
    # Newest first. The first item we just inserted is `newer`.
    ordered = [item["query_raw"] for item in body["items"]]
    assert ordered.index("newer") < ordered.index("older")
    for item in body["items"]:
        if item["query_raw"] == "older":
            assert item["entity_count"] == 1
        if item["query_raw"] == "newer":
            assert item["entity_count"] == 0


@pytest.mark.asyncio
async def test_list_jobs_filter_by_status(db_session: AsyncSession, override_session) -> None:
    db_session.add(Job(query_raw="ok", limit=10, budget_cap_usd=1.0, status="succeeded"))
    db_session.add(Job(query_raw="run", limit=10, budget_cap_usd=1.0, status="running"))
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs?status=running")
    body = resp.json()
    assert all(item["status"] == "running" for item in body["items"])
    assert body["total"] >= 1


@pytest.mark.asyncio
async def test_export_csv_rejected_while_running(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(query_raw="cafes in Lisbon", limit=10, budget_cap_usd=5.0, status="running")
    db_session.add(job)
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/export.csv")
    assert resp.status_code == 409
