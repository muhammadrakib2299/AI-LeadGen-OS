"""HTTP tests for /blacklist and the /privacy/opt-out → Blacklist wire-up."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blacklist
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
async def test_post_blacklist_adds_email_entry(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/blacklist",
            json={"email": "opt-out@example.com", "reason": "user request"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "opt-out@example.com"
    assert body["domain"] is None

    row = (
        await db_session.execute(
            select(Blacklist).where(Blacklist.email == "opt-out@example.com")
        )
    ).scalar_one()
    assert row.reason == "user request"


@pytest.mark.asyncio
async def test_post_blacklist_adds_domain_entry(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/blacklist",
            json={"domain": "  BlockedCorp.EXAMPLE  "},
        )
    assert resp.status_code == 201
    # Domain is lowercased and stripped so enforcement lookups match consistently.
    assert resp.json()["domain"] == "blockedcorp.example"


@pytest.mark.asyncio
async def test_post_blacklist_requires_email_or_domain(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/blacklist", json={"reason": "nothing"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_blacklist_rejects_both_email_and_domain(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/blacklist",
            json={"email": "a@b.example", "domain": "b.example"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_blacklist_duplicate_returns_409(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/blacklist", json={"email": "dupe@example.com"})
        second = await client.post("/blacklist", json={"email": "dupe@example.com"})
    assert first.status_code == 201
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_list_blacklist_supports_substring_search(
    db_session: AsyncSession, override_session
) -> None:
    db_session.add(Blacklist(email="alice@example.com", reason="r"))
    db_session.add(Blacklist(domain="blockedcorp.example", reason="r"))
    db_session.add(Blacklist(email="bob@other.example", reason="r"))
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/blacklist?q=blocked")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["domain"] == "blockedcorp.example"


@pytest.mark.asyncio
async def test_delete_blacklist_removes_entry(
    db_session: AsyncSession, override_session
) -> None:
    row = Blacklist(email="x@example.com", reason="r")
    db_session.add(row)
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/blacklist/{row.id}")
    assert resp.status_code == 204
    assert await db_session.get(Blacklist, row.id) is None


@pytest.mark.asyncio
async def test_delete_blacklist_404(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/blacklist/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_opt_out_persists_to_blacklist(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/privacy/opt-out",
            json={"email": "subject@example.com", "reason": "GDPR Art. 17"},
        )
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"

    row = (
        await db_session.execute(
            select(Blacklist).where(Blacklist.email == "subject@example.com")
        )
    ).scalar_one()
    assert row.reason == "GDPR Art. 17"


@pytest.mark.asyncio
async def test_opt_out_is_idempotent(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/privacy/opt-out", json={"email": "repeat@example.com"}
        )
        second = await client.post(
            "/privacy/opt-out", json={"email": "repeat@example.com"}
        )
    assert first.status_code == 202
    assert second.status_code == 202
    # Exactly one row, not two.
    rows = (
        await db_session.execute(
            select(Blacklist).where(Blacklist.email == "repeat@example.com")
        )
    ).scalars().all()
    assert len(rows) == 1
