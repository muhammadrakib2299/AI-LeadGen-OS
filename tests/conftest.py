"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import User
from app.main import app


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def override_current_user() -> Iterator[User]:
    """Pretend every test caller is authenticated.

    Phase 4 gated most routers behind `get_current_user`. Existing tests
    (jobs, review, templates, blacklist) pre-date auth and would otherwise
    401. Tests that want to exercise the unauth path can pop this override
    explicitly:

        app.dependency_overrides.pop(get_current_user, None)
    """
    fake = User(
        id=uuid4(),
        tenant_id=uuid4(),
        email="test@example.com",
        password_hash="not-a-real-hash",
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async session wrapped in an outer transaction that is rolled back at end.

    Uses `join_transaction_mode="create_savepoint"` so `session.commit()` calls
    inside application code become savepoint releases rather than real commits,
    letting the outer rollback wipe everything the test (or HTTP handler) wrote.

    Skips the test if Postgres is unreachable.
    """
    engine = create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
    )
    try:
        async with engine.connect() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for integration test: {exc}")

    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()
    await engine.dispose()
