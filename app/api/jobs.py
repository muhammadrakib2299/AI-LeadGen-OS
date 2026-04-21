"""Jobs API — submit, poll, export.

Background execution uses asyncio.create_task for Phase 1 MVP. Phase 2
replaces this with Celery / RQ workers for durability across restarts.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import Entity, Job, RawFetch, User
from app.db.session import get_session
from app.services.export import entities_to_csv
from app.services.queue import get_redis_pool

log = get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreateRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=100, ge=1, le=1000)
    budget_cap_usd: float = Field(default=5.0, gt=0, le=100.0)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class BulkSeedEntity(BaseModel):
    name: str | None = Field(default=None, max_length=512)
    website: str | None = Field(default=None, max_length=512)
    domain: str | None = Field(default=None, max_length=255)


class BulkJobCreateRequest(BaseModel):
    entities: list[BulkSeedEntity] = Field(min_length=1, max_length=500)
    budget_cap_usd: float = Field(default=5.0, gt=0, le=100.0)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


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
    places_discovered: int
    places_processed: int
    progress_percent: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


class JobEntity(BaseModel):
    id: UUID
    name: str
    domain: str | None
    website: str | None
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    country: str | None
    category: str | None
    socials: dict[str, Any] | None
    quality_score: int | None
    review_status: str
    field_sources: dict[str, Any]
    created_at: datetime


class JobEntityListResponse(BaseModel):
    items: list[JobEntity]
    total: int
    limit: int
    offset: int


class SourceFriction(BaseModel):
    """Per-source summary of outcomes for this job's raw_fetches.

    `slow_reason` is a plain-language hint the UI can surface — "rate limited"
    when 429s dominate, "unavailable" for 5xx, "all good" when every call
    returned 2xx.
    """

    source: str
    calls: int
    success: int
    rate_limited: int  # 429
    server_errors: int  # 5xx
    avg_duration_ms: int | None
    slow_reason: str | None


class JobDiagnosticsResponse(BaseModel):
    job_id: UUID
    sources: list[SourceFriction]
    retry_after_hits: int  # total 429s across all sources
    summary: str  # one-liner suitable for UI display


def _to_response(job: Job, entity_count: int) -> JobResponse:
    progress: float | None
    if job.places_discovered > 0:
        progress = round(
            100.0 * min(job.places_processed, job.places_discovered) / job.places_discovered,
            1,
        )
    else:
        progress = None
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
        places_discovered=job.places_discovered,
        places_processed=job.places_processed,
        progress_percent=progress,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _count_entities(session: AsyncSession, job_id: UUID) -> int:
    stmt = select(func.count()).select_from(Entity).where(Entity.job_id == job_id)
    return int((await session.execute(stmt)).scalar_one())


async def _get_job_for_tenant(
    session: AsyncSession, job_id: UUID, tenant_id: UUID
) -> Job | None:
    """Fetch a job belonging to `tenant_id`, or None. Used in place of
    `session.get(Job, id)` everywhere we want tenant-scoped access — a
    foreign-tenant lookup must 404, never 403, so we don't leak existence.
    """
    stmt = select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id)
    return (await session.execute(stmt)).scalar_one_or_none()


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
    response: Response,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JobResponse:
    if payload.idempotency_key:
        # Idempotency is scoped per-tenant so two tenants can safely use the
        # same upstream key (e.g. n8n workflow IDs) without colliding.
        existing = (
            await session.execute(
                select(Job).where(
                    Job.idempotency_key == payload.idempotency_key,
                    Job.tenant_id == current_user.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            response.status_code = 200
            log.info(
                "job_idempotent_hit",
                job_id=str(existing.id),
                idempotency_key=payload.idempotency_key,
            )
            return _to_response(
                existing, entity_count=await _count_entities(session, existing.id)
            )

    job = Job(
        tenant_id=current_user.tenant_id,
        query_raw=payload.query,
        limit=payload.limit,
        budget_cap_usd=payload.budget_cap_usd,
        idempotency_key=payload.idempotency_key,
        status="pending",
    )
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(Job).where(
                    Job.idempotency_key == payload.idempotency_key,
                    Job.tenant_id == current_user.tenant_id,
                )
            )
        ).scalar_one()
        response.status_code = 200
        return _to_response(existing, entity_count=await _count_entities(session, existing.id))
    await session.refresh(job)

    await _run_in_background(job.id)

    log.info("job_created", job_id=str(job.id), query=payload.query[:120])
    return _to_response(job, entity_count=0)


MAX_CSV_BYTES = 1_000_000  # 1 MB upload cap — 500 rows fit comfortably
MAX_BULK_ROWS = 500

# Accept common header aliases so callers don't have to rename columns.
CSV_HEADER_ALIASES: dict[str, str] = {
    "name": "name",
    "company": "name",
    "company_name": "name",
    "organization": "name",
    "website": "website",
    "url": "website",
    "homepage": "website",
    "domain": "domain",
    "hostname": "domain",
}


@router.post("/bulk", response_model=JobResponse, status_code=201)
async def create_bulk_job(
    payload: BulkJobCreateRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JobResponse:
    seeds = _seeds_from_json(payload.entities)
    return await _create_bulk_job(
        session=session,
        response=response,
        seeds=seeds,
        budget_cap_usd=payload.budget_cap_usd,
        idempotency_key=payload.idempotency_key,
        tenant_id=current_user.tenant_id,
    )


@router.post("/bulk/csv", response_model=JobResponse, status_code=201)
async def create_bulk_job_from_csv(
    response: Response,
    file: UploadFile = File(...),
    budget_cap_usd: float = Form(default=5.0, gt=0, le=100.0),
    idempotency_key: str | None = Form(default=None, min_length=8, max_length=128),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JobResponse:
    raw = await file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds {MAX_CSV_BYTES} bytes")
    seeds = _parse_csv_upload(raw)
    return await _create_bulk_job(
        session=session,
        response=response,
        seeds=seeds,
        budget_cap_usd=budget_cap_usd,
        idempotency_key=idempotency_key,
        tenant_id=current_user.tenant_id,
    )


def _seeds_from_json(entities: list[BulkSeedEntity]) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    for row in entities:
        website = (row.website or "").strip() or None
        domain = (row.domain or "").strip() or None
        if not website and not domain:
            raise HTTPException(
                status_code=422,
                detail="every entity must include at least one of website or domain",
            )
        seeds.append(
            {"name": (row.name or "").strip() or None, "website": website, "domain": domain}
        )
    return seeds


def _parse_csv_upload(raw: bytes) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig")  # strip BOM if Excel emitted one
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"CSV is not UTF-8: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV has no header row")

    header_map: dict[str, str] = {}
    for raw_name in reader.fieldnames:
        key = (raw_name or "").strip().lower()
        if key in CSV_HEADER_ALIASES:
            header_map[raw_name] = CSV_HEADER_ALIASES[key]
    if "website" not in header_map.values() and "domain" not in header_map.values():
        raise HTTPException(
            status_code=422,
            detail="CSV must include a website or domain column",
        )

    seeds: list[dict[str, Any]] = []
    for i, row in enumerate(reader):
        if i >= MAX_BULK_ROWS:
            raise HTTPException(
                status_code=422, detail=f"CSV exceeds {MAX_BULK_ROWS} rows"
            )
        mapped: dict[str, Any] = {"name": None, "website": None, "domain": None}
        for raw_col, canonical in header_map.items():
            value = (row.get(raw_col) or "").strip() or None
            if value is not None:
                mapped[canonical] = value
        if not mapped["website"] and not mapped["domain"]:
            continue  # skip blank/partial rows silently — common in real-world CSVs
        seeds.append(mapped)

    if not seeds:
        raise HTTPException(
            status_code=422,
            detail="no rows with a website or domain were found",
        )
    return seeds


async def _create_bulk_job(
    *,
    session: AsyncSession,
    response: Response,
    seeds: list[dict[str, Any]],
    budget_cap_usd: float,
    idempotency_key: str | None,
    tenant_id: UUID,
) -> JobResponse:
    if idempotency_key:
        existing = (
            await session.execute(
                select(Job).where(
                    Job.idempotency_key == idempotency_key,
                    Job.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            response.status_code = 200
            return _to_response(
                existing, entity_count=await _count_entities(session, existing.id)
            )

    job = Job(
        tenant_id=tenant_id,
        query_raw=f"bulk_enrichment({len(seeds)} entities)",
        limit=len(seeds),
        budget_cap_usd=budget_cap_usd,
        idempotency_key=idempotency_key,
        status="pending",
        job_type="bulk_enrichment",
        seed_entities=seeds,
    )
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(Job).where(
                    Job.idempotency_key == idempotency_key,
                    Job.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        response.status_code = 200
        return _to_response(existing, entity_count=await _count_entities(session, existing.id))
    await session.refresh(job)

    await _run_in_background(job.id)

    log.info("bulk_job_created", job_id=str(job.id), seed_count=len(seeds))
    return _to_response(job, entity_count=0)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JobListResponse:
    filters = [Job.tenant_id == current_user.tenant_id]
    if status:
        filters.append(Job.status == status)

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
    current_user: User = Depends(get_current_user),
) -> JobResponse:
    job = await _get_job_for_tenant(session, job_id, current_user.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _to_response(job, entity_count=await _count_entities(session, job.id))


@router.get("/{job_id}/entities", response_model=JobEntityListResponse)
async def list_job_entities(
    job_id: UUID,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    review_status: str | None = Query(default=None),
    include_duplicates: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JobEntityListResponse:
    job = await _get_job_for_tenant(session, job_id, current_user.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    filters = [Entity.job_id == job_id]
    if review_status is not None:
        filters.append(Entity.review_status == review_status)
    if not include_duplicates:
        filters.append(Entity.duplicate_of.is_(None))

    total = int(
        (
            await session.execute(select(func.count()).select_from(Entity).where(*filters))
        ).scalar_one()
    )

    stmt = (
        select(Entity)
        .where(*filters)
        .order_by(Entity.quality_score.desc().nullslast(), Entity.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    entities = (await session.execute(stmt)).scalars().all()
    items = [
        JobEntity(
            id=e.id,
            name=e.name,
            domain=e.domain,
            website=e.website,
            email=e.email,
            phone=e.phone,
            address=e.address,
            city=e.city,
            country=e.country,
            category=e.category,
            socials=e.socials,
            quality_score=e.quality_score,
            review_status=e.review_status,
            field_sources=e.field_sources or {},
            created_at=e.created_at,
        )
        for e in entities
    ]
    return JobEntityListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{job_id}/diagnostics", response_model=JobDiagnosticsResponse)
async def job_diagnostics(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JobDiagnosticsResponse:
    """Explain why a job ran slow (or why it failed) from the audit log.

    This is a read-only summary of `raw_fetches` rows belonging to the job:
    counts per source, 429/5xx tallies, and average duration. No retention
    concerns here — we're only aggregating rows that still exist.
    """
    job = await _get_job_for_tenant(session, job_id, current_user.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    rows = (
        await session.execute(
            select(RawFetch).where(RawFetch.job_id == job_id)
        )
    ).scalars().all()

    per_source: dict[str, dict[str, Any]] = {}
    for r in rows:
        bucket = per_source.setdefault(
            r.source_slug,
            {"calls": 0, "success": 0, "rate_limited": 0, "server_errors": 0, "durations": []},
        )
        bucket["calls"] += 1
        status_code = r.response_status or 0
        if 200 <= status_code < 300:
            bucket["success"] += 1
        elif status_code == 429:
            bucket["rate_limited"] += 1
        elif status_code >= 500:
            bucket["server_errors"] += 1
        if r.duration_ms is not None:
            bucket["durations"].append(r.duration_ms)

    sources: list[SourceFriction] = []
    total_429 = 0
    for slug, b in sorted(per_source.items()):
        total_429 += b["rate_limited"]
        durations: list[int] = b["durations"]
        avg_ms = int(sum(durations) / len(durations)) if durations else None
        reason: str | None = None
        if b["rate_limited"] and b["rate_limited"] >= b["success"]:
            reason = "rate limited — honoring Retry-After"
        elif b["server_errors"]:
            reason = "upstream returning 5xx"
        elif b["calls"] > 0 and b["success"] < b["calls"]:
            reason = "mixed failures"
        sources.append(
            SourceFriction(
                source=slug,
                calls=b["calls"],
                success=b["success"],
                rate_limited=b["rate_limited"],
                server_errors=b["server_errors"],
                avg_duration_ms=avg_ms,
                slow_reason=reason,
            )
        )

    if not sources:
        summary = "No external calls recorded yet."
    elif total_429:
        summary = f"Rate-limited on {total_429} call(s); the pipeline is deliberately waiting."
    elif any(s.server_errors for s in sources):
        summary = "One or more sources returned 5xx errors — pipeline retried where safe."
    else:
        summary = "All upstream calls succeeded."

    return JobDiagnosticsResponse(
        job_id=job_id,
        sources=sources,
        retry_after_hits=total_429,
        summary=summary,
    )


@router.get("/{job_id}/export.csv")
async def export_job_csv(
    job_id: UUID,
    include_rejected: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    job = await _get_job_for_tenant(session, job_id, current_user.tenant_id)
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
