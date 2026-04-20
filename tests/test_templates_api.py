"""HTTP tests for the /templates endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SearchTemplate
from app.db.session import get_session
from app.main import app


@pytest.fixture
def override_session(db_session: AsyncSession):
    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_template_creates_row(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/templates",
            json={
                "name": "EU SaaS startups",
                "query": "B2B SaaS companies in Berlin",
                "default_limit": 50,
                "default_budget_cap_usd": 3.0,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "EU SaaS startups"
    assert body["default_limit"] == 50
    assert body["default_budget_cap_usd"] == 3.0

    row = await db_session.get(SearchTemplate, body["id"])
    assert row is not None
    assert row.query == "B2B SaaS companies in Berlin"


@pytest.mark.asyncio
async def test_post_template_duplicate_name_returns_409(
    db_session: AsyncSession, override_session
) -> None:
    payload = {"name": "dupe", "query": "restaurants in Paris"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/templates", json=payload)
        second = await client.post("/templates", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_list_templates_returns_newest_first(
    db_session: AsyncSession, override_session
) -> None:
    from datetime import UTC, datetime, timedelta

    t_old = datetime.now(UTC) - timedelta(hours=1)
    t_new = datetime.now(UTC)
    db_session.add(
        SearchTemplate(
            name="older",
            query="cafes in Lisbon",
            default_limit=10,
            default_budget_cap_usd=1.0,
            created_at=t_old,
            updated_at=t_old,
        )
    )
    db_session.add(
        SearchTemplate(
            name="newer",
            query="bars in Madrid",
            default_limit=10,
            default_budget_cap_usd=1.0,
            created_at=t_new,
            updated_at=t_new,
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/templates")
    assert resp.status_code == 200
    body = resp.json()
    names = [item["name"] for item in body["items"]]
    assert names.index("newer") < names.index("older")
    assert body["total"] == len(body["items"])


@pytest.mark.asyncio
async def test_delete_template_removes_row(
    db_session: AsyncSession, override_session
) -> None:
    row = SearchTemplate(
        name="to-delete",
        query="restaurants in Paris",
        default_limit=10,
        default_budget_cap_usd=1.0,
    )
    db_session.add(row)
    await db_session.flush()
    tid = row.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/templates/{tid}")
    assert resp.status_code == 204

    assert await db_session.get(SearchTemplate, tid) is None


@pytest.mark.asyncio
async def test_delete_template_404(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/templates/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_template_rejects_short_query(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/templates", json={"name": "x", "query": "a"})
    assert resp.status_code == 422
