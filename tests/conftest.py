"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async session for integration tests.

    Creates a fresh engine per test (pytest-asyncio uses function-scoped event
    loops by default, so a module-level cached engine bound to a prior loop
    breaks). The test's writes are rolled back on teardown.

    Skips the test if Postgres is unreachable.
    """
    engine = create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        poolclass=None,  # NullPool via default for async; keep pool simple
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for integration test: {exc}")

    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()
    await engine.dispose()
