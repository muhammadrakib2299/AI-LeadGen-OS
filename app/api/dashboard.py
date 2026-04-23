"""GET /dashboard — operator overview at a glance.

Distinct from /reports/attribution (which answers "where did this set of
leads come from?") and from /status (circuit breakers). This endpoint
exists so the operator can answer "what's happening on my account RIGHT
NOW" without flipping between three pages:

- queue: how many of my jobs are pending / running
- sources_24h: each source's last-24h call count, success rate, cost
- top_cost_jobs_24h: the 10 costliest jobs in the last 24h
- recent_failures: the last 10 failed jobs

All sections are tenant-scoped so multi-tenant isolation holds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import Job, RawFetch, User
from app.db.session import get_session

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

WINDOW = timedelta(hours=24)


class QueueDepth(BaseModel):
    pending: int
    running: int
    succeeded_24h: int
    failed_24h: int


class SourceHealth(BaseModel):
    source: str
    calls: int
    success_rate: float  # 0.0–1.0
    cost_usd: float
    avg_duration_ms: int | None


class CostlyJob(BaseModel):
    job_id: str
    query_raw: str
    status: str
    cost_usd: float
    created_at: datetime


class RecentFailure(BaseModel):
    job_id: str
    query_raw: str
    error: str | None
    finished_at: datetime | None


class DashboardResponse(BaseModel):
    queue: QueueDepth
    sources_24h: list[SourceHealth]
    top_cost_jobs_24h: list[CostlyJob]
    recent_failures: list[RecentFailure]


async def _queue_depth(db: AsyncSession, tenant_id) -> QueueDepth:
    cutoff = datetime.now(UTC) - WINDOW
    # Counts in one round-trip via FILTER aggregates.
    row = (
        await db.execute(
            select(
                func.count().filter(Job.status == "pending").label("pending"),
                func.count().filter(Job.status == "running").label("running"),
                func.count()
                .filter(Job.status == "succeeded", Job.finished_at >= cutoff)
                .label("succeeded_24h"),
                func.count()
                .filter(Job.status == "failed", Job.finished_at >= cutoff)
                .label("failed_24h"),
            ).where(Job.tenant_id == tenant_id)
        )
    ).one()
    return QueueDepth(
        pending=int(row.pending or 0),
        running=int(row.running or 0),
        succeeded_24h=int(row.succeeded_24h or 0),
        failed_24h=int(row.failed_24h or 0),
    )


async def _sources_24h(db: AsyncSession, tenant_id) -> list[SourceHealth]:
    cutoff = datetime.now(UTC) - WINDOW
    # raw_fetches.job_id → jobs.tenant_id; LEFT-ish via inner join is fine
    # because we don't care about rows without a job (none should exist).
    success_case = case(
        (RawFetch.response_status.between(200, 399), 1), else_=0
    )
    rows = (
        await db.execute(
            select(
                RawFetch.source_slug.label("source"),
                func.count().label("calls"),
                func.sum(success_case).label("successes"),
                func.coalesce(func.sum(RawFetch.cost_usd), 0).label("cost"),
                func.avg(RawFetch.duration_ms).label("avg_ms"),
            )
            .join(Job, RawFetch.job_id == Job.id)
            .where(
                Job.tenant_id == tenant_id,
                RawFetch.created_at >= cutoff,
            )
            .group_by(RawFetch.source_slug)
            .order_by(func.count().desc())
        )
    ).all()
    out: list[SourceHealth] = []
    for r in rows:
        calls = int(r.calls or 0)
        successes = int(r.successes or 0)
        out.append(
            SourceHealth(
                source=r.source,
                calls=calls,
                # Saturate to 1.0 at calls==0 so the UI doesn't divide by zero.
                success_rate=(successes / calls) if calls else 1.0,
                cost_usd=float(r.cost or 0),
                avg_duration_ms=int(r.avg_ms) if r.avg_ms is not None else None,
            )
        )
    return out


async def _top_cost_jobs_24h(db: AsyncSession, tenant_id) -> list[CostlyJob]:
    cutoff = datetime.now(UTC) - WINDOW
    rows = (
        await db.execute(
            select(Job)
            .where(Job.tenant_id == tenant_id, Job.created_at >= cutoff)
            .order_by(Job.cost_usd.desc())
            .limit(10)
        )
    ).scalars().all()
    return [
        CostlyJob(
            job_id=str(j.id),
            query_raw=j.query_raw,
            status=j.status,
            cost_usd=float(j.cost_usd or 0),
            created_at=j.created_at,
        )
        for j in rows
        if (j.cost_usd or 0) > 0
    ]


async def _recent_failures(db: AsyncSession, tenant_id) -> list[RecentFailure]:
    rows = (
        await db.execute(
            select(Job)
            .where(Job.tenant_id == tenant_id, Job.status == "failed")
            .order_by(Job.finished_at.desc().nulls_last(), Job.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    return [
        RecentFailure(
            job_id=str(j.id),
            query_raw=j.query_raw,
            error=j.error,
            finished_at=j.finished_at,
        )
        for j in rows
    ]


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> DashboardResponse:
    queue = await _queue_depth(db, current_user.tenant_id)
    sources = await _sources_24h(db, current_user.tenant_id)
    costly = await _top_cost_jobs_24h(db, current_user.tenant_id)
    failures = await _recent_failures(db, current_user.tenant_id)
    return DashboardResponse(
        queue=queue,
        sources_24h=sources,
        top_cost_jobs_24h=costly,
        recent_failures=failures,
    )
