"""Integration endpoints — currently HubSpot only.

- POST   /integrations/hubspot/token    — store (or rotate) a private-app token
- GET    /integrations/hubspot          — is a token configured?
- DELETE /integrations/hubspot          — disconnect
- POST   /integrations/hubspot/export/{job_id}
           — push the job's non-rejected entities as HubSpot Contacts
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import Entity, HubspotIntegration, Job, User
from app.db.session import get_session
from app.services.hubspot import export_contacts

log = get_logger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])


class HubspotTokenRequest(BaseModel):
    access_token: str = Field(min_length=10, max_length=1024)


class HubspotStatusResponse(BaseModel):
    connected: bool
    last_export_at: datetime | None = None


class ExportResponse(BaseModel):
    attempted: int
    created: int
    errors: list[str]


async def _get_integration(
    db: AsyncSession, tenant_id: UUID
) -> HubspotIntegration | None:
    return (
        await db.execute(
            select(HubspotIntegration).where(HubspotIntegration.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


@router.post("/hubspot/token", response_model=HubspotStatusResponse)
async def set_hubspot_token(
    body: HubspotTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> HubspotStatusResponse:
    existing = await _get_integration(db, current_user.tenant_id)
    if existing:
        # Rotate the token in place so old references stay valid.
        existing.access_token = body.access_token
    else:
        db.add(
            HubspotIntegration(
                tenant_id=current_user.tenant_id,
                access_token=body.access_token,
            )
        )
    await db.commit()
    integration = await _get_integration(db, current_user.tenant_id)
    assert integration is not None
    log.info("hubspot_token_stored", tenant_id=str(current_user.tenant_id))
    return HubspotStatusResponse(
        connected=True,
        last_export_at=integration.last_export_at,
    )


@router.get("/hubspot", response_model=HubspotStatusResponse)
async def get_hubspot_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> HubspotStatusResponse:
    integration = await _get_integration(db, current_user.tenant_id)
    if integration is None:
        return HubspotStatusResponse(connected=False, last_export_at=None)
    return HubspotStatusResponse(
        connected=True, last_export_at=integration.last_export_at
    )


@router.delete("/hubspot", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_hubspot(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    integration = await _get_integration(db, current_user.tenant_id)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not connected")
    await db.delete(integration)
    await db.commit()


@router.post(
    "/hubspot/export/{job_id}",
    response_model=ExportResponse,
    status_code=status.HTTP_200_OK,
)
async def export_job_to_hubspot(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ExportResponse:
    integration = await _get_integration(db, current_user.tenant_id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HubSpot is not connected for this tenant. POST a token first.",
        )
    # Scope the job to the caller's tenant. Foreign jobs 404 (not 403) so
    # we don't confirm the job exists in another tenant.
    job = (
        await db.execute(
            select(Job).where(
                Job.id == job_id, Job.tenant_id == current_user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    entities = (
        await db.execute(
            select(Entity).where(
                Entity.job_id == job.id,
                Entity.duplicate_of.is_(None),
                Entity.review_status.not_in(("rejected", "duplicate")),
            )
        )
    ).scalars().all()

    result = await export_contacts(integration.access_token, list(entities))
    integration.last_export_at = datetime.now(UTC)
    await db.commit()
    log.info(
        "hubspot_export_done",
        tenant_id=str(current_user.tenant_id),
        job_id=str(job_id),
        attempted=result.attempted,
        created=result.created,
        errors=len(result.errors),
    )
    return ExportResponse(
        attempted=result.attempted, created=result.created, errors=result.errors
    )
