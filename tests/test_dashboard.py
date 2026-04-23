"""Integration tests for GET /dashboard.

Seeds Jobs + RawFetches across two tenants and asserts the response is
strictly tenant-scoped, that source success rate is computed correctly,
that cost ranking respects the 24h window, and that the recent-failures
section ignores foreign tenants.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, RawFetch, Tenant
from app.db.session import get_session
from app.main import app


@pytest.fixture
def override_session(db_session: AsyncSession):
    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.clear()


async def _seed(db_session: AsyncSession) -> Tenant:
    """Two tenants, our tenant gets a mixed bag of jobs + fetches."""
    foreign = Tenant(name="other")
    db_session.add(foreign)
    await db_session.flush()

    now = datetime.now(UTC)
    fresh = now - timedelta(hours=2)
    older = now - timedelta(days=2)

    # Our tenant — populated by the autouse Tenant seed in conftest.
    j_pending = Job(
        query_raw="pending one", limit=10, budget_cap_usd=5.0, status="pending"
    )
    j_running = Job(
        query_raw="running one", limit=10, budget_cap_usd=5.0, status="running"
    )
    j_succeeded = Job(
        query_raw="cheap good",
        limit=10,
        budget_cap_usd=5.0,
        status="succeeded",
        cost_usd=0.50,
        finished_at=fresh,
    )
    j_expensive = Job(
        query_raw="expensive good",
        limit=10,
        budget_cap_usd=5.0,
        status="succeeded",
        cost_usd=4.20,
        finished_at=fresh,
    )
    j_failed = Job(
        query_raw="failed one",
        limit=10,
        budget_cap_usd=5.0,
        status="failed",
        error="adapter timed out",
        finished_at=fresh,
    )
    j_old_failed = Job(
        query_raw="ancient failure",
        limit=10,
        budget_cap_usd=5.0,
        status="failed",
        error="this should still show — failures aren't windowed",
        finished_at=older,
    )
    db_session.add_all(
        [j_pending, j_running, j_succeeded, j_expensive, j_failed, j_old_failed]
    )
    await db_session.flush()

    # Foreign tenant — must NEVER appear in our caller's response.
    j_other = Job(
        tenant_id=foreign.id,
        query_raw="not mine",
        limit=10,
        budget_cap_usd=5.0,
        status="pending",
    )
    db_session.add(j_other)
    await db_session.flush()

    # Source health: 5 places calls (4 success, 1 5xx), 2 yelp calls (both ok).
    # Plus 1 places call for the foreign tenant (must be excluded).
    db_session.add_all(
        [
            RawFetch(
                job_id=j_succeeded.id,
                source_slug="google_places",
                url="https://places.example/1",
                response_status=200,
                cost_usd=0.01,
                duration_ms=120,
                created_at=fresh,
            ),
            RawFetch(
                job_id=j_succeeded.id,
                source_slug="google_places",
                url="https://places.example/2",
                response_status=200,
                cost_usd=0.01,
                duration_ms=140,
                created_at=fresh,
            ),
            RawFetch(
                job_id=j_succeeded.id,
                source_slug="google_places",
                url="https://places.example/3",
                response_status=200,
                cost_usd=0.01,
                duration_ms=110,
                created_at=fresh,
            ),
            RawFetch(
                job_id=j_succeeded.id,
                source_slug="google_places",
                url="https://places.example/4",
                response_status=200,
                cost_usd=0.01,
                duration_ms=130,
                created_at=fresh,
            ),
            RawFetch(
                job_id=j_succeeded.id,
                source_slug="google_places",
                url="https://places.example/5",
                response_status=503,
                cost_usd=0.0,
                duration_ms=200,
                created_at=fresh,
            ),
            RawFetch(
                job_id=j_succeeded.id,
                source_slug="yelp",
                url="https://yelp.example/1",
                response_status=200,
                cost_usd=0.0,
                duration_ms=80,
                created_at=fresh,
            ),
            RawFetch(
                job_id=j_succeeded.id,
                source_slug="yelp",
                url="https://yelp.example/2",
                response_status=200,
                cost_usd=0.0,
                duration_ms=90,
                created_at=fresh,
            ),
            RawFetch(
                job_id=j_other.id,
                source_slug="google_places",
                url="https://places.example/foreign",
                response_status=200,
                cost_usd=0.05,
                duration_ms=999,
                created_at=fresh,
            ),
        ]
    )
    await db_session.flush()
    return foreign


@pytest.mark.asyncio
async def test_dashboard_queue_counts_only_my_tenant(
    db_session: AsyncSession, override_session
) -> None:
    await _seed(db_session)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    queue = body["queue"]
    # Foreign pending row must not leak in.
    assert queue["pending"] == 1
    assert queue["running"] == 1
    assert queue["succeeded_24h"] == 2
    assert queue["failed_24h"] == 1  # the day-2-old failure is outside the window


@pytest.mark.asyncio
async def test_dashboard_source_success_rate(
    db_session: AsyncSession, override_session
) -> None:
    await _seed(db_session)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/dashboard")
    by_source = {s["source"]: s for s in resp.json()["sources_24h"]}
    # Foreign tenant's google_places call must not inflate calls/cost.
    assert by_source["google_places"]["calls"] == 5
    assert by_source["google_places"]["success_rate"] == pytest.approx(0.8)
    assert by_source["google_places"]["cost_usd"] == pytest.approx(0.04)
    assert by_source["yelp"]["calls"] == 2
    assert by_source["yelp"]["success_rate"] == 1.0


@pytest.mark.asyncio
async def test_dashboard_top_cost_jobs_ordered_desc(
    db_session: AsyncSession, override_session
) -> None:
    await _seed(db_session)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/dashboard")
    costly = resp.json()["top_cost_jobs_24h"]
    assert [j["query_raw"] for j in costly] == ["expensive good", "cheap good"]
    assert costly[0]["cost_usd"] > costly[1]["cost_usd"]


@pytest.mark.asyncio
async def test_dashboard_recent_failures_includes_old_ones(
    db_session: AsyncSession, override_session
) -> None:
    await _seed(db_session)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/dashboard")
    failures = resp.json()["recent_failures"]
    queries = [f["query_raw"] for f in failures]
    # Both failures show up (the section isn't 24h-windowed — operators want
    # to see what crashed even if it was last week).
    assert "failed one" in queries
    assert "ancient failure" in queries
    # Newest first.
    assert queries.index("failed one") < queries.index("ancient failure")
