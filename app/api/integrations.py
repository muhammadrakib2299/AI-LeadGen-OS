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
from app.db.models import Entity, HubspotIntegration, Job, S3ExportDestination, User
from app.db.session import get_session
from app.services.export import entities_to_csv
from app.services.hubspot import export_contacts
from app.services.s3_export import put_object

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


# ── S3 export destination ──────────────────────────────────────────────


class S3ConfigRequest(BaseModel):
    bucket: str = Field(min_length=3, max_length=255)
    region: str = Field(min_length=2, max_length=64)
    prefix: str = Field(default="", max_length=255)
    access_key_id: str = Field(min_length=10, max_length=255)
    secret_access_key: str = Field(min_length=10, max_length=1024)


class S3StatusResponse(BaseModel):
    connected: bool
    bucket: str | None = None
    region: str | None = None
    prefix: str | None = None
    last_export_at: datetime | None = None


class S3UploadResponse(BaseModel):
    s3_uri: str
    row_count: int


async def _get_s3_destination(
    db: AsyncSession, tenant_id: UUID
) -> S3ExportDestination | None:
    return (
        await db.execute(
            select(S3ExportDestination).where(S3ExportDestination.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


@router.post("/s3", response_model=S3StatusResponse)
async def set_s3_destination(
    body: S3ConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> S3StatusResponse:
    existing = await _get_s3_destination(db, current_user.tenant_id)
    if existing:
        existing.bucket = body.bucket
        existing.region = body.region
        existing.prefix = body.prefix
        existing.access_key_id = body.access_key_id
        existing.secret_access_key = body.secret_access_key
    else:
        db.add(
            S3ExportDestination(
                tenant_id=current_user.tenant_id,
                bucket=body.bucket,
                region=body.region,
                prefix=body.prefix,
                access_key_id=body.access_key_id,
                secret_access_key=body.secret_access_key,
            )
        )
    await db.commit()
    dest = await _get_s3_destination(db, current_user.tenant_id)
    assert dest is not None
    log.info(
        "s3_destination_set", tenant_id=str(current_user.tenant_id), bucket=dest.bucket
    )
    return S3StatusResponse(
        connected=True,
        bucket=dest.bucket,
        region=dest.region,
        prefix=dest.prefix,
        last_export_at=dest.last_export_at,
    )


@router.get("/s3", response_model=S3StatusResponse)
async def get_s3_destination(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> S3StatusResponse:
    dest = await _get_s3_destination(db, current_user.tenant_id)
    if dest is None:
        return S3StatusResponse(connected=False)
    return S3StatusResponse(
        connected=True,
        bucket=dest.bucket,
        region=dest.region,
        prefix=dest.prefix,
        last_export_at=dest.last_export_at,
    )


@router.delete("/s3", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_s3(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    dest = await _get_s3_destination(db, current_user.tenant_id)
    if dest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not connected")
    await db.delete(dest)
    await db.commit()


@router.post("/s3/export/{job_id}", response_model=S3UploadResponse)
async def export_job_to_s3(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> S3UploadResponse:
    dest = await _get_s3_destination(db, current_user.tenant_id)
    if dest is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="S3 destination is not connected. POST credentials first.",
        )
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

    csv_body = entities_to_csv(entities).encode("utf-8")
    key_prefix = (dest.prefix or "").rstrip("/")
    key = f"{key_prefix}/{job.id}.csv" if key_prefix else f"{job.id}.csv"

    result = await put_object(
        bucket=dest.bucket,
        region=dest.region,
        access_key_id=dest.access_key_id,
        secret_access_key=dest.secret_access_key,
        key=key,
        body=csv_body,
    )
    dest.last_export_at = datetime.now(UTC)
    await db.commit()
    log.info(
        "s3_export_done",
        tenant_id=str(current_user.tenant_id),
        job_id=str(job_id),
        s3_uri=result.s3_uri,
    )
    return S3UploadResponse(s3_uri=result.s3_uri, row_count=len(entities))
