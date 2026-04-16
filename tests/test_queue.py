"""Verify POST /jobs enqueues a run_job arq task (no real Redis needed)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.main import app


@pytest.mark.asyncio
async def test_post_jobs_enqueues_run_job(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=None)

    async def _get_pool() -> Any:
        return fake_pool

    from app.api import jobs as jobs_api

    monkeypatch.setattr(jobs_api, "get_redis_pool", _get_pool)

    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/jobs",
                json={"query": "cafes in Berlin", "limit": 3, "budget_cap_usd": 1.0},
            )
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        fake_pool.enqueue_job.assert_awaited_once()
        call_args = fake_pool.enqueue_job.call_args
        assert call_args.args[0] == "run_job"
        assert call_args.args[1] == job_id
    finally:
        app.dependency_overrides.clear()
