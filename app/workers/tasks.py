"""arq task functions. Imported by app.workers.worker for registration."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.db.models import Job
from app.db.session import session_scope
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
