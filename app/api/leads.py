"""Lead pipeline operations on individual entities.

Separate router from /jobs because the lead pipeline is a per-row workflow,
not a per-job concept. Tenant scoping is enforced via the parent Job — an
entity belongs to a job, which belongs to a tenant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import Entity, Job, User
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/leads", tags=["leads"])

# Intentional small vocabulary; expand here when sales adds states.
LeadStatus = Literal["new", "contacted", "responded", "converted", "lost"]
ALLOWED_STATUSES: set[str] = {"new", "contacted", "responded", "converted", "lost"}


class LeadStatusUpdateRequest(BaseModel):
    lead_status: LeadStatus
    lead_notes: str | None = Field(default=None, max_length=2000)


class LeadResponse(BaseModel):
    id: UUID
    job_id: UUID
    name: str
    email: str | None
    lead_status: str
    lead_status_changed_at: datetime | None
    lead_notes: str | None


def _to_response(e: Entity) -> LeadResponse:
    return LeadResponse(
        id=e.id,
        job_id=e.job_id,
        name=e.name,
        email=e.email,
        lead_status=e.lead_status,
        lead_status_changed_at=e.lead_status_changed_at,
        lead_notes=e.lead_notes,
    )


async def _get_entity_for_tenant(
    db: AsyncSession, entity_id: UUID, tenant_id: UUID
) -> Entity:
    """Resolve an entity belonging to a job belonging to the caller's tenant.

    Done as one join so a foreign tenant gets a 404, not a query revealing
    the entity's existence.
    """
    stmt = (
        select(Entity)
        .join(Job, Entity.job_id == Job.id)
        .where(Entity.id == entity_id, Job.tenant_id == tenant_id)
    )
    entity = (await db.execute(stmt)).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lead not found")
    return entity


@router.patch("/{entity_id}", response_model=LeadResponse)
async def update_lead_status(
    entity_id: UUID,
    body: LeadStatusUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LeadResponse:
    if body.lead_status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"lead_status must be one of {sorted(ALLOWED_STATUSES)}",
        )
    entity = await _get_entity_for_tenant(db, entity_id, current_user.tenant_id)
    moved = entity.lead_status != body.lead_status
    entity.lead_status = body.lead_status
    if moved:
        entity.lead_status_changed_at = datetime.now(UTC)
    if body.lead_notes is not None:
        entity.lead_notes = body.lead_notes
    await db.commit()
    await db.refresh(entity)
    log.info(
        "lead_status_updated",
        entity_id=str(entity_id),
        lead_status=entity.lead_status,
        moved=moved,
    )
    return _to_response(entity)


@router.get("/{entity_id}", response_model=LeadResponse)
async def get_lead(
    entity_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> LeadResponse:
    entity = await _get_entity_for_tenant(db, entity_id, current_user.tenant_id)
    return _to_response(entity)
