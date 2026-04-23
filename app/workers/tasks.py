"""arq task functions. Imported by app.workers.worker for registration."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Job
from app.db.session import session_scope
from app.services.reverify import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_MAX_AGE_DAYS,
    reverify_stale_entities,
)
from app.services.runner_factory import make_production_runner

log = get_logger(__name__)


async def run_job(ctx: dict[str, Any], job_id_str: str) -> str:
    """Execute the Phase 1 pipeline for one job. Returns the final status."""
    job_id = UUID(job_id_str)
    log.info("worker_run_job_begin", job_id=job_id_str)
    try:
        async with session_scope() as session:
            job = await session.get(Job, job_id)
            if job is None:
                log.warning("worker_run_job_not_found", job_id=job_id_str)
                return "not_found"
            try:
                runner, cleanup = await make_production_runner(session)
            except RuntimeError as exc:
                job.status = "failed"
                job.error = str(exc)
                return "failed"
            try:
                await runner.run(job)
            finally:
                await cleanup()
            return job.status
    except Exception:
        log.exception("worker_run_job_crashed", job_id=job_id_str)
        raise


async def daily_retention_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    """arq cron entrypoint for compliance.md §6 retention windows.

    Wraps the same `sweep()` the standalone CLI calls — keeps the logic
    in one place. Imported lazily so the module-level import graph stays
    small for the worker that just runs jobs.
    """
    from scripts.retention_sweep import sweep

    log.info("cron_retention_sweep_begin")
    async with session_scope() as session:
        counts = await sweep(session, dry_run=False)
    log.info("cron_retention_sweep_done", **counts)
    return counts


async def daily_reverify_pass(ctx: dict[str, Any]) -> dict[str, int]:
    """arq cron entrypoint for the aged-entity reverification pass.

    Defaults match `scripts/reverify_aged.py` so the cron behaves the
    same as a manual run.
    """
    settings = get_settings()
    log.info("cron_reverify_begin")
    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": settings.default_user_agent},
    ) as http, session_scope() as session:
        result = await reverify_stale_entities(
            session,
            http,
            max_age_days=DEFAULT_MAX_AGE_DAYS,
            limit=DEFAULT_BATCH_LIMIT,
        )
    summary = {
        "scanned": result.scanned,
        "websites_dead": result.websites_dead,
        "emails_invalid": result.emails_invalid,
        "phones_invalid": result.phones_invalid,
        "errors": len(result.errors),
    }
    log.info("cron_reverify_done", **summary)
    return summary
