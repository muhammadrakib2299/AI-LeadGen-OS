"""arq worker entry point.

Run with:
    uv run arq app.workers.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from app.core.logging import configure_logging, get_logger
from app.services.queue import redis_settings
from app.workers.tasks import run_job


async def on_startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    get_logger(__name__).info("worker_started")


async def on_shutdown(ctx: dict[str, Any]) -> None:
    get_logger(__name__).info("worker_stopped")


class WorkerSettings:
    functions = [run_job]
    redis_settings = redis_settings()
    max_jobs = 5
    job_timeout = 60 * 15  # 15 min per job (per overview.md ETA budget)
    keep_result_forever = False
    on_startup = on_startup
    on_shutdown = on_shutdown
