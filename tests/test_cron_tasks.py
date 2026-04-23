"""Tests for the arq cron task wrappers.

The underlying logic (sweep, reverify_stale_entities) is covered by its
own test files. These tests pin the wrapper contract: the right defaults
get passed, the result shape matches the cron logger expectations, and
exceptions surface (don't get swallowed) so arq retries work.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.reverify import ReverifyResult
from app.workers import tasks


@pytest.fixture(autouse=True)
def fake_session(monkeypatch: pytest.MonkeyPatch):
    """Stand in for session_scope so cron tasks don't need a real DB."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield None  # the wrappers pass session straight through to mocked callees

    monkeypatch.setattr(tasks, "session_scope", _scope)


async def test_daily_retention_sweep_returns_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_sweep(session, *, dry_run: bool) -> dict[str, int]:
        captured["dry_run"] = dry_run
        return {"raw_fetches": 7, "exports": 2, "entities": 0, "yelp_payload_nulled": 1}

    # The wrapper imports lazily; patch the module the wrapper imports from.
    import scripts.retention_sweep as sweep_module

    monkeypatch.setattr(sweep_module, "sweep", fake_sweep)

    result = await tasks.daily_retention_sweep(ctx={})
    assert captured["dry_run"] is False
    assert result == {
        "raw_fetches": 7,
        "exports": 2,
        "entities": 0,
        "yelp_payload_nulled": 1,
    }


async def test_daily_retention_sweep_propagates_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(session, *, dry_run: bool) -> dict[str, int]:
        raise RuntimeError("disk full")

    import scripts.retention_sweep as sweep_module

    monkeypatch.setattr(sweep_module, "sweep", boom)

    with pytest.raises(RuntimeError, match="disk full"):
        await tasks.daily_retention_sweep(ctx={})


async def test_daily_reverify_pass_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_reverify(session, http, *, max_age_days: int, limit: int) -> ReverifyResult:
        captured["max_age_days"] = max_age_days
        captured["limit"] = limit
        result = ReverifyResult()
        result.scanned = 12
        result.websites_dead = 3
        result.emails_invalid = 1
        result.phones_invalid = 0
        result.errors = ["one transient failure"]
        return result

    monkeypatch.setattr(tasks, "reverify_stale_entities", fake_reverify)

    result = await tasks.daily_reverify_pass(ctx={})
    assert result == {
        "scanned": 12,
        "websites_dead": 3,
        "emails_invalid": 1,
        "phones_invalid": 0,
        "errors": 1,
    }
    # Wrapper must pass the script-level defaults; if these change the
    # cron schedule's load profile changes — fail loudly.
    assert captured["max_age_days"] == tasks.DEFAULT_MAX_AGE_DAYS
    assert captured["limit"] == tasks.DEFAULT_BATCH_LIMIT


async def test_daily_reverify_pass_propagates_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(session, http, *, max_age_days: int, limit: int) -> ReverifyResult:
        raise RuntimeError("redis down")

    monkeypatch.setattr(tasks, "reverify_stale_entities", boom)

    with pytest.raises(RuntimeError, match="redis down"):
        await tasks.daily_reverify_pass(ctx={})


def test_worker_settings_register_cron_jobs() -> None:
    """Pin the schedule so a typo in the worker module fails CI loudly."""
    from app.workers.worker import WorkerSettings

    cron_funcs = [c.coroutine.__name__ for c in WorkerSettings.cron_jobs]
    assert "daily_retention_sweep" in cron_funcs
    assert "daily_reverify_pass" in cron_funcs
    assert tasks.daily_retention_sweep in WorkerSettings.functions
    assert tasks.daily_reverify_pass in WorkerSettings.functions
