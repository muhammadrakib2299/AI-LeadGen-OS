"""Reports — `attribution` is the only one for now.

Answers "where did our leads (and our spend) come from?" by aggregating
the audit log (raw_fetches) and entities for the caller's tenant within
a window. Joined-on-the-fly rather than precomputed — Postgres handles
the volume the dashboard sees, and we avoid an aggregation cron.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import Entity, Job, RawFetch, User
from app.db.session import get_session

router = APIRouter(prefix="/reports", tags=["reports"])


class SourceRow(BaseModel):
    source: str
    calls: int
    success_calls: int
    cost_usd: float
    avg_duration_ms: int | None


class AttributionResponse(BaseModel):
    window_days: int
    total_jobs: int
    total_entities: int
    total_cost_usd: float
    avg_quality_score: float | None
    sources: list[SourceRow]


@router.get("/attribution", response_model=AttributionResponse)
async def attribution(
    window_days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> AttributionResponse:
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    # Job/entity rollups for this tenant in the window.
    job_filter = (Job.tenant_id == current_user.tenant_id) & (Job.created_at >= cutoff)
    total_jobs = int(
        (
            await db.execute(select(func.count()).select_from(Job).where(job_filter))
        ).scalar_one()
    )
    entity_stats = (
        await db.execute(
            select(
                func.count().label("n"),
                func.coalesce(func.avg(Entity.quality_score), 0.0).label("avg_q"),
            )
            .select_from(Entity)
            .join(Job, Entity.job_id == Job.id)
            .where(job_filter, Entity.duplicate_of.is_(None))
        )
    ).one()
    total_entities = int(entity_stats.n)
    avg_quality = float(entity_stats.avg_q) if total_entities else None

    # Per-source rollup from raw_fetches joined to jobs (tenant scope via job).
    rows = (
        await db.execute(
            select(
                RawFetch.source_slug,
                func.count().label("calls"),
                func.sum(
                    case(
                        (
                            (RawFetch.response_status >= 200)
                            & (RawFetch.response_status < 300),
                            1,
                        ),
                        else_=0,
                    )
                ).label("ok"),
                func.coalesce(func.sum(RawFetch.cost_usd), 0.0).label("cost"),
                func.avg(RawFetch.duration_ms).label("avg_ms"),
            )
            .select_from(RawFetch)
            .join(Job, RawFetch.job_id == Job.id)
            .where(job_filter)
            .group_by(RawFetch.source_slug)
            .order_by(RawFetch.source_slug)
        )
    ).all()

    sources: list[SourceRow] = []
    total_cost = 0.0
    for r in rows:
        cost = float(r.cost or 0.0)
        total_cost += cost
        sources.append(
            SourceRow(
                source=r.source_slug,
                calls=int(r.calls),
                success_calls=int(r.ok or 0),
                cost_usd=round(cost, 6),
                avg_duration_ms=int(r.avg_ms) if r.avg_ms is not None else None,
            )
        )

    return AttributionResponse(
        window_days=window_days,
        total_jobs=total_jobs,
        total_entities=total_entities,
        total_cost_usd=round(total_cost, 6),
        avg_quality_score=round(avg_quality, 1) if avg_quality is not None else None,
        sources=sources,
    )
