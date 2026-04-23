"""HTTP tests for /api-keys and X-API-Key authentication on protected routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.api_keys import generate_api_key
from app.db.models import ApiKey, User
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
async def persisted_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    # Point the auth dep at this real user so /api-keys foreign-key lookups work.
    app.dependency_overrides[get_current_user] = lambda: user
    return user


@pytest.fixture
def no_auth_override():
    app.dependency_overrides.pop(get_current_user, None)
    yield


@pytest.mark.asyncio
async def test_create_key_returns_plaintext_once(
    db_session: AsyncSession, override_session, persisted_user: User
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api-keys", json={"name": "integration"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "integration"
    assert body["key"].startswith("lg_live_")
    assert body["prefix"] == body["key"][:12]

    # Plaintext is never stored.
    rows = (await db_session.execute(select(ApiKey))).scalars().all()
    assert len(rows) == 1
    assert rows[0].key_hash != body["key"]
    assert rows[0].user_id == persisted_user.id


@pytest.mark.asyncio
async def test_list_keys_shows_only_caller_keys(
    db_session: AsyncSession, override_session, persisted_user: User
) -> None:
    # Seed a key belonging to a different user — must not leak into the list.
    stranger = User(
        id=uuid4(), email=f"other-{uuid4().hex[:6]}@example.com", password_hash="x"
    )
    db_session.add(stranger)
    await db_session.flush()
    _, prefix, key_hash = generate_api_key()
    db_session.add(
        ApiKey(user_id=stranger.id, name="stranger", prefix=prefix, key_hash=key_hash)
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api-keys", json={"name": "mine"})
        resp = await client.get("/api-keys")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "mine"


@pytest.mark.asyncio
async def test_revoke_key_marks_revoked(
    db_session: AsyncSession, override_session, persisted_user: User
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api-keys", json={"name": "temp"})).json()
        resp = await client.delete(f"/api-keys/{created['id']}")
    assert resp.status_code == 204

    row = (
        await db_session.execute(select(ApiKey).where(ApiKey.id == created["id"]))
    ).scalar_one()
    assert row.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_other_users_key_is_404(
    db_session: AsyncSession, override_session, persisted_user: User
) -> None:
    # Someone else's key — our user should get 404, not 204.
    stranger = User(
        id=uuid4(), email=f"s-{uuid4().hex[:6]}@example.com", password_hash="x"
    )
    db_session.add(stranger)
    await db_session.flush()
    _, prefix, key_hash = generate_api_key()
    foreign = ApiKey(user_id=stranger.id, name="nope", prefix=prefix, key_hash=key_hash)
    db_session.add(foreign)
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api-keys/{foreign.id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_key_header_authenticates_protected_route(
    db_session: AsyncSession,
    override_session,
    persisted_user: User,
    no_auth_override,
) -> None:
    # Insert a real key for the user, then hit /jobs with X-API-Key and expect 200.
    plaintext, prefix, key_hash = generate_api_key()
    db_session.add(
        ApiKey(user_id=persisted_user.id, name="robot", prefix=prefix, key_hash=key_hash)
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs", headers={"X-API-Key": plaintext})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_revoked_api_key_is_rejected(
    db_session: AsyncSession,
    override_session,
    persisted_user: User,
    no_auth_override,
) -> None:
    plaintext, prefix, key_hash = generate_api_key()
    db_session.add(
        ApiKey(
            user_id=persisted_user.id,
            name="old",
            prefix=prefix,
            key_hash=key_hash,
            revoked_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs", headers={"X-API-Key": plaintext})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_api_key_is_rejected(
    db_session: AsyncSession,
    override_session,
    persisted_user: User,
    no_auth_override,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs", headers={"X-API-Key": "lg_live_" + "0" * 64})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_key_updates_last_used_at(
    db_session: AsyncSession,
    override_session,
    persisted_user: User,
    no_auth_override,
) -> None:
    plaintext, prefix, key_hash = generate_api_key()
    row = ApiKey(user_id=persisted_user.id, name="track", prefix=prefix, key_hash=key_hash)
    db_session.add(row)
    await db_session.flush()
    assert row.last_used_at is None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs", headers={"X-API-Key": plaintext})
    assert resp.status_code == 200
    await db_session.refresh(row)
    assert row.last_used_at is not None



@pytest.mark.asyncio
async def test_rotate_returns_new_plaintext_and_grace_window(
    db_session: AsyncSession,
    override_session,
    persisted_user: User,
) -> None:
    plaintext_old, prefix, key_hash = generate_api_key()
    old = ApiKey(
        user_id=persisted_user.id,
        name="prod",
        prefix=prefix,
        key_hash=key_hash,
    )
    db_session.add(old)
    await db_session.flush()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api-keys/{old.id}/rotate")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"].startswith("lg_live_")
    assert body["key"] != plaintext_old
    assert body["name"] == "prod"
    assert body["expires_at"] is None  # the NEW key has no expiry

    await db_session.refresh(old)
    assert old.expires_at is not None
    # ~24h grace, give or take execution time
    delta = old.expires_at - datetime.now(UTC)
    assert timedelta(hours=23, minutes=55) < delta <= timedelta(hours=24)
    assert str(old.rotated_to_id) == body["id"]


@pytest.mark.asyncio
async def test_rotate_unknown_key_is_404(
    db_session: AsyncSession, override_session, persisted_user: User
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api-keys/{uuid4()}/rotate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rotate_revoked_key_is_409(
    db_session: AsyncSession, override_session, persisted_user: User
) -> None:
    _, prefix, key_hash = generate_api_key()
    revoked = ApiKey(
        user_id=persisted_user.id,
        name="dead",
        prefix=prefix,
        key_hash=key_hash,
        revoked_at=datetime.now(UTC),
    )
    db_session.add(revoked)
    await db_session.flush()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api-keys/{revoked.id}/rotate")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_rotate_already_rotated_key_is_409(
    db_session: AsyncSession, override_session, persisted_user: User
) -> None:
    _, prefix, key_hash = generate_api_key()
    old = ApiKey(
        user_id=persisted_user.id,
        name="prod",
        prefix=prefix,
        key_hash=key_hash,
    )
    db_session.add(old)
    await db_session.flush()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(f"/api-keys/{old.id}/rotate")
        assert first.status_code == 201
        second = await client.post(f"/api-keys/{old.id}/rotate")
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_old_key_works_during_grace_then_rejected_after(
    db_session: AsyncSession,
    override_session,
    persisted_user: User,
    no_auth_override,
) -> None:
    plaintext_old, prefix, key_hash = generate_api_key()
    old = ApiKey(
        user_id=persisted_user.id,
        name="prod",
        prefix=prefix,
        key_hash=key_hash,
        # Already past expiry: simulates "grace window has lapsed".
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(old)
    await db_session.flush()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/jobs", headers={"X-API-Key": plaintext_old})
    assert resp.status_code == 401

    # Same key, expiry pushed into the future — should authenticate again.
    old.expires_at = datetime.now(UTC) + timedelta(hours=1)
    await db_session.flush()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/jobs", headers={"X-API-Key": plaintext_old})
    assert resp.status_code == 200
