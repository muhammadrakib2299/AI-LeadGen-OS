"""Tests for /status (circuit breakers) and /jobs/{id}/diagnostics."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, RawFetch
from app.db.session import get_session
from app.main import app
from app.services.places import _PLACES_BREAKER


@pytest.fixture
def override_session(db_session: AsyncSession):
    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_status_returns_breaker_snapshots() -> None:
    await _PLACES_BREAKER.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "ok"
    names = {c["name"] for c in body["circuits"]}
    # Every registered dependency should be in the snapshot.
    assert {"google_places", "yelp", "opencorporates", "anthropic", "dns_mx"} <= names


@pytest.mark.asyncio
async def test_diagnostics_summarizes_raw_fetches(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(query_raw="q", limit=10, budget_cap_usd=5.0, status="succeeded")
    db_session.add(job)
    await db_session.flush()

    db_session.add_all(
        [
            RawFetch(
                job_id=job.id,
                source_slug="google_places",
                url="https://places",
                response_status=200,
                duration_ms=100,
                legal_basis="legitimate_interest",
            ),
            RawFetch(
                job_id=job.id,
                source_slug="google_places",
                url="https://places",
                response_status=429,
                duration_ms=50,
                legal_basis="legitimate_interest",
            ),
            RawFetch(
                job_id=job.id,
                source_slug="yelp",
                url="https://yelp",
                response_status=500,
                duration_ms=200,
                legal_basis="legitimate_interest",
            ),
        ]
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/diagnostics")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retry_after_hits"] == 1
    assert "Rate-limited" in body["summary"]

    by_source = {s["source"]: s for s in body["sources"]}
    places = by_source["google_places"]
    assert places["calls"] == 2
    assert places["success"] == 1
    assert places["rate_limited"] == 1
    assert places["avg_duration_ms"] == 75

    yelp = by_source["yelp"]
    assert yelp["server_errors"] == 1
    assert "5xx" in yelp["slow_reason"]


@pytest.mark.asyncio
async def test_diagnostics_unknown_job_404(
    db_session: AsyncSession, override_session
) -> None:
    from uuid import uuid4

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{uuid4()}/diagnostics")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_diagnostics_all_success_reports_clean(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(query_raw="q", limit=10, budget_cap_usd=5.0, status="succeeded")
    db_session.add(job)
    await db_session.flush()

    db_session.add(
        RawFetch(
            job_id=job.id,
            source_slug="google_places",
            url="https://places",
            response_status=200,
            duration_ms=100,
            legal_basis="legitimate_interest",
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/diagnostics")
    body = resp.json()
    assert body["retry_after_hits"] == 0
    assert "All upstream calls succeeded" in body["summary"]
    assert body["sources"][0]["slow_reason"] is None
