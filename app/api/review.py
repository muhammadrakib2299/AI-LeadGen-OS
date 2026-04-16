"""Review queue API — low-confidence entities are surfaced here for human approval.

Per overview.md §3.1 item 8: "Review queue — low-confidence rows surfaced to a
human before export."
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Entity, Job
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/review", tags=["review"])


ReviewAction = Literal["approve", "reject"]


class ReviewEntity(BaseModel):
    id: UUID
    job_id: UUID
    job_query: str
    name: str
    website: str | None
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    country: str | None
    category: str | None
    quality_score: int | None
    review_status: str
    field_sources: dict[str, Any]
    created_at: datetime


class ReviewList(BaseModel):
    items: list[ReviewEntity]
    total: int
    limit: int
    offset: int


class ReviewDecision(BaseModel):
    id: UUID
    review_status: str


def _to_review_entity(entity: Entity, job_query: str) -> ReviewEntity:
    return ReviewEntity(
        id=entity.id,
        job_id=entity.job_id,
        job_query=job_query,
        name=entity.name,
        website=entity.website,
        email=entity.email,
        phone=entity.phone,
        address=entity.address,
        city=entity.city,
        country=entity.country,
        category=entity.category,
        quality_score=entity.quality_score,
        review_status=entity.review_status,
        field_sources=entity.field_sources or {},
        created_at=entity.created_at,
    )


@router.get("", response_model=ReviewList)
async def list_review_queue(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    job_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ReviewList:
    filters = [Entity.review_status == "review"]
    if job_id is not None:
        filters.append(Entity.job_id == job_id)

    total = int(
        (
            await session.execute(select(func.count()).select_from(Entity).where(*filters))
        ).scalar_one()
    )

    stmt = (
        select(Entity, Job.query_raw)
        .join(Job, Job.id == Entity.job_id)
        .where(*filters)
        .order_by(Entity.quality_score.asc().nullsfirst(), Entity.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    items = [_to_review_entity(ent, q) for ent, q in rows]
    return ReviewList(items=items, total=total, limit=limit, offset=offset)


@router.post("/{entity_id}/approve", response_model=ReviewDecision)
async def approve_entity(
    entity_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ReviewDecision:
    return await _set_review_status(session, entity_id, "approved")


@router.post("/{entity_id}/reject", response_model=ReviewDecision)
async def reject_entity(
    entity_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ReviewDecision:
    return await _set_review_status(session, entity_id, "rejected")


async def _set_review_status(
    session: AsyncSession, entity_id: UUID, new_status: str
) -> ReviewDecision:
    entity = await session.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")
    entity.review_status = new_status
    await session.flush()
    log.info(
        "review_decision",
        entity_id=str(entity_id),
        review_status=new_status,
    )
    return ReviewDecision(id=entity.id, review_status=entity.review_status)
