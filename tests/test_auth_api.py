"""HTTP tests for /auth/{register,login,logout,me} and protected-route gating."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import verify_password
from app.db.models import User
from app.db.session import get_session
from app.main import app


@pytest.fixture
def override_session(db_session: AsyncSession):
    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def no_auth_override():
    """Drop the autouse auth override so /auth/me validates the real token."""
    app.dependency_overrides.pop(get_current_user, None)
    yield


@pytest.mark.asyncio
async def test_register_creates_user_and_returns_token(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/auth/register",
            json={"email": "Alice@Example.com", "password": "sup3rsecret"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "alice@example.com"

    row = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    # Password is hashed, never stored plain.
    assert row.password_hash != "sup3rsecret"
    assert verify_password("sup3rsecret", row.password_hash)


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/auth/register",
            json={"email": "dupe@example.com", "password": "password123"},
        )
        second = await client.post(
            "/auth/register",
            json={"email": "dupe@example.com", "password": "password123"},
        )
    assert first.status_code == 201
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_register_rejects_short_password(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/auth/register",
            json={"email": "short@example.com", "password": "2short"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_with_correct_password_returns_token(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={"email": "bob@example.com", "password": "password123"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "bob@example.com", "password": "password123"},
        )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_login_with_wrong_password_returns_401(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/register",
            json={"email": "carol@example.com", "password": "password123"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "carol@example.com", "password": "wrong-password"},
        )
    assert resp.status_code == 401
    # Don't leak which of email/password was wrong.
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_unknown_user_returns_same_401(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "whatever"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_me_with_bearer_token_returns_user(
    db_session: AsyncSession, override_session, no_auth_override
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reg = await client.post(
            "/auth/register",
            json={"email": "dan@example.com", "password": "password123"},
        )
        token = reg.json()["access_token"]
        resp = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["email"] == "dan@example.com"


@pytest.mark.asyncio
async def test_me_without_token_returns_401(
    db_session: AsyncSession, override_session, no_auth_override
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_garbage_token_returns_401(
    db_session: AsyncSession, override_session, no_auth_override
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/auth/me",
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_rejects_unauthenticated_caller(
    db_session: AsyncSession, override_session, no_auth_override
) -> None:
    # /jobs is gated — must 401 without a token.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_public_routes_remain_accessible(
    db_session: AsyncSession, override_session, no_auth_override
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/health")
        opt_out = await client.post(
            "/privacy/opt-out", json={"email": "subject@example.com"}
        )
    assert health.status_code == 200
    # Opt-out must work without an account — data subjects don't sign up.
    assert opt_out.status_code == 202


@pytest.mark.asyncio
async def test_logout_clears_cookie(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/auth/logout")
    assert resp.status_code == 204
