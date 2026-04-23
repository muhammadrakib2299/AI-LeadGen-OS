"""arq worker entry point.

Run with:
    uv run arq app.workers.worker.WorkerSettings

The worker also runs scheduled jobs (compliance.md §6 retention windows
and aged-entity reverification) via arq's cron support — no separate
scheduler process required. Times are UTC and chosen for low traffic.
"""

from __future__ import annotations

from typing import Any

from arq import cron

from app.core.logging import configure_logging, get_logger
from app.services.queue import redis_settings
from app.workers.tasks import daily_retention_sweep, daily_reverify_pass, run_job


async def on_startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    get_logger(__name__).info("worker_started")


async def on_shutdown(ctx: dict[str, Any]) -> None:
    get_logger(__name__).info("worker_stopped")


class WorkerSettings:
    functions = [run_job, daily_retention_sweep, daily_reverify_pass]
    cron_jobs = [
        # Retention sweep at 03:00 UTC every day. Runs first so reverify
        # operates on the post-purge dataset.
        cron(daily_retention_sweep, hour=3, minute=0),
        # Aged-entity reverification at 04:00 UTC every day.
        cron(daily_reverify_pass, hour=4, minute=0),
    ]
    redis_settings = redis_settings()
    max_jobs = 5
    job_timeout = 60 * 15  # 15 min per job (per overview.md ETA budget)
    keep_result_forever = False
    on_startup = on_startup
    on_shutdown = on_shutdown
