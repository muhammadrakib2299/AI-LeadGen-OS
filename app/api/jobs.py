"""Jobs API — submit, poll, export.

Background execution uses asyncio.create_task for Phase 1 MVP. Phase 2
replaces this with Celery / RQ workers for durability across restarts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Entity, Job
from app.db.session import get_session
from app.services.export import entities_to_csv
from app.services.queue import get_redis_pool

log = get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreateRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=100, ge=1, le=1000)
    budget_cap_usd: float = Field(default=5.0, gt=0, le=100.0)


class JobResponse(BaseModel):
    id: UUID
    status: str
    query_raw: str
    query_validated: dict[str, Any] | None
    limit: int
    budget_cap_usd: float
    cost_usd: float
    error: str | None
    entity_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


def _to_response(job: Job, entity_count: int) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status,
        query_raw=job.query_raw,
        query_validated=job.query_validated,
        limit=job.limit,
        budget_cap_usd=float(job.budget_cap_usd),
        cost_usd=float(job.cost_usd),
        error=job.error,
        entity_count=entity_count,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _count_entities(session: AsyncSession, job_id: UUID) -> int:
    stmt = select(func.count()).select_from(Entity).where(Entity.job_id == job_id)
    return int((await session.execute(stmt)).scalar_one())


async def _run_in_background(job_id: UUID) -> None:
    """Enqueue the job to arq. A separate worker process runs the pipeline.

    Tests monkey-patch this function to keep runs in-process.
    """
    pool = await get_redis_pool()
    await pool.enqueue_job("run_job", str(job_id))
    log.info("job_enqueued", job_id=str(job_id))


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    payload: JobCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    job = Job(
        query_raw=payload.query,
        limit=payload.limit,
        budget_cap_usd=payload.budget_cap_usd,
        status="pending",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    await _run_in_background(job.id)

    log.info("job_created", job_id=str(job.id), query=payload.query[:120])
    return _to_response(job, entity_count=0)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> JobListResponse:
    filters = [Job.status == status] if status else []

    total = int(
        (await session.execute(select(func.count()).select_from(Job).where(*filters))).scalar_one()
    )

    count_subq = (
        select(func.count())
        .select_from(Entity)
        .where(Entity.job_id == Job.id)
        .correlate(Job)
        .scalar_subquery()
    )
    stmt = (
        select(Job, count_subq.label("entity_count"))
        .where(*filters)
        .order_by(Job.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    items = [_to_response(job, int(entity_count)) for job, entity_count in rows]
    return JobListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _to_response(job, entity_count=await _count_entities(session, job.id))


@router.get("/{job_id}/export.csv")
async def export_job_csv(
    job_id: UUID,
    include_rejected: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> Response:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in {"pending", "running"}:
        raise HTTPException(status_code=409, detail=f"job is {job.status}; not yet exportable")

    stmt = select(Entity).where(Entity.job_id == job.id)
    if not include_rejected:
        stmt = stmt.where(Entity.review_status.not_in(("rejected", "duplicate")))
    entities = (await session.execute(stmt)).scalars().all()
    csv_body = entities_to_csv(entities)
    filename = f"leadgen-{job.id}.csv"
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
