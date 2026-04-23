"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)

# Set the encryption key BEFORE importing app modules that bake it in.
# Tests insert Entities with phone/address (encrypted columns); without
# a key the writer raises EncryptionNotConfiguredError. A throwaway key
# per test process is fine — the test DB rolls back at the end of every
# test anyway.
os.environ.setdefault("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.api.deps import get_current_user  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.crypto import reset_cache_for_tests  # noqa: E402
from app.db.models import Tenant, User  # noqa: E402
from app.main import app  # noqa: E402

# Clear caches so the freshly-set env var is picked up. Settings is
# lru_cached and may have already been built by an import side-effect.
get_settings.cache_clear()
reset_cache_for_tests()


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
async def db_session(override_current_user: User) -> AsyncIterator[AsyncSession]:
    """Async session wrapped in an outer transaction that is rolled back at end.

    Uses `join_transaction_mode="create_savepoint"` so `session.commit()` calls
    inside application code become savepoint releases rather than real commits,
    letting the outer rollback wipe everything the test (or HTTP handler) wrote.

    Skips the test if Postgres is unreachable.

    Multi-tenant bridge for legacy tests: this fixture seeds a Tenant row
    matching `override_current_user.tenant_id` and registers a before-flush
    listener that backfills `tenant_id` on any pending row that has the
    attribute set to None. Pre-tenant tests (test_jobs_api, test_review_api,
    test_dedupe, etc.) keep working without per-call edits, while production
    code paths — which never see this listener — still hit the NOT NULL
    constraint if they forget tenant_id.
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
        # Wipe any rows leaked by earlier test runs (commits that escaped
        # the savepoint protection). All inside the outer transaction, so
        # the truncate itself rolls back at end of test — net effect is
        # "every test sees a clean slate" without permanently destroying
        # the dev DB. CASCADE handles FK deps; we list the parent tables
        # only.
        await conn.execute(
            text(
                "TRUNCATE TABLE tenants, users, jobs, raw_fetches, "
                "blacklist, search_templates RESTART IDENTITY CASCADE"
            )
        )
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        default_tenant_id = override_current_user.tenant_id

        def _backfill_tenant(sess, _flush_context, _instances) -> None:
            for obj in sess.new:
                # Backfill any tenant-scoped row that didn't supply one.
                # Skip Tenant itself — its `tenant_id` would be the SELF id.
                if isinstance(obj, Tenant):
                    continue
                if hasattr(obj, "tenant_id") and getattr(obj, "tenant_id", None) is None:
                    obj.tenant_id = default_tenant_id

        event.listen(session.sync_session, "before_flush", _backfill_tenant)

        # Seed the tenant row the listener will point at. Done after listener
        # registration so it's harmless either way.
        session.add(Tenant(id=default_tenant_id, name="test-tenant"))
        await session.flush()

        try:
            yield session
        finally:
            event.remove(session.sync_session, "before_flush", _backfill_tenant)
            await session.close()
            await outer.rollback()
    await engine.dispose()
