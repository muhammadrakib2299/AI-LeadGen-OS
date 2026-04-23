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
from app.db.models import (
    Entity,
    GoogleSheetsDestination,
    HubspotIntegration,
    Job,
    PipedriveIntegration,
    S3ExportDestination,
    User,
)
from app.db.session import get_session
from app.services.export import entities_to_csv
from app.services.google_sheets import append_entities, parse_service_account
from app.services.hubspot import export_contacts
from app.services.pipedrive import export_persons
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


# ── Pipedrive ─────────────────────────────────────────────────────────


class PipedriveTokenRequest(BaseModel):
    api_token: str = Field(min_length=10, max_length=1024)
    company_domain: str | None = Field(default=None, max_length=128)


class PipedriveStatusResponse(BaseModel):
    connected: bool
    company_domain: str | None = None
    last_export_at: datetime | None = None


async def _get_pipedrive_integration(
    db: AsyncSession, tenant_id: UUID
) -> PipedriveIntegration | None:
    return (
        await db.execute(
            select(PipedriveIntegration).where(
                PipedriveIntegration.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()


@router.post("/pipedrive/token", response_model=PipedriveStatusResponse)
async def set_pipedrive_token(
    body: PipedriveTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PipedriveStatusResponse:
    existing = await _get_pipedrive_integration(db, current_user.tenant_id)
    if existing:
        existing.api_token = body.api_token
        existing.company_domain = body.company_domain
    else:
        db.add(
            PipedriveIntegration(
                tenant_id=current_user.tenant_id,
                api_token=body.api_token,
                company_domain=body.company_domain,
            )
        )
    await db.commit()
    integration = await _get_pipedrive_integration(db, current_user.tenant_id)
    assert integration is not None
    log.info("pipedrive_token_stored", tenant_id=str(current_user.tenant_id))
    return PipedriveStatusResponse(
        connected=True,
        company_domain=integration.company_domain,
        last_export_at=integration.last_export_at,
    )


@router.get("/pipedrive", response_model=PipedriveStatusResponse)
async def get_pipedrive_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PipedriveStatusResponse:
    integration = await _get_pipedrive_integration(db, current_user.tenant_id)
    if integration is None:
        return PipedriveStatusResponse(connected=False)
    return PipedriveStatusResponse(
        connected=True,
        company_domain=integration.company_domain,
        last_export_at=integration.last_export_at,
    )


@router.delete("/pipedrive", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_pipedrive(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    integration = await _get_pipedrive_integration(db, current_user.tenant_id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not connected"
        )
    await db.delete(integration)
    await db.commit()


@router.post(
    "/pipedrive/export/{job_id}",
    response_model=ExportResponse,
    status_code=status.HTTP_200_OK,
)
async def export_job_to_pipedrive(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ExportResponse:
    integration = await _get_pipedrive_integration(db, current_user.tenant_id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pipedrive is not connected for this tenant. POST a token first.",
        )
    job = (
        await db.execute(
            select(Job).where(
                Job.id == job_id, Job.tenant_id == current_user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )

    entities = (
        await db.execute(
            select(Entity).where(
                Entity.job_id == job.id,
                Entity.duplicate_of.is_(None),
                Entity.review_status.not_in(("rejected", "duplicate")),
            )
        )
    ).scalars().all()

    result = await export_persons(
        integration.api_token,
        list(entities),
        company_domain=integration.company_domain,
    )
    integration.last_export_at = datetime.now(UTC)
    await db.commit()
    log.info(
        "pipedrive_export_done",
        tenant_id=str(current_user.tenant_id),
        job_id=str(job_id),
        attempted=result.attempted,
        created=result.created,
        errors=len(result.errors),
    )
    return ExportResponse(
        attempted=result.attempted, created=result.created, errors=result.errors
    )


# ── Google Sheets ─────────────────────────────────────────────────────


class GoogleSheetsConfigRequest(BaseModel):
    service_account_json: str = Field(min_length=20, max_length=8192)
    spreadsheet_id: str = Field(min_length=10, max_length=128)
    worksheet_name: str = Field(default="Leads", min_length=1, max_length=128)


class GoogleSheetsStatusResponse(BaseModel):
    connected: bool
    spreadsheet_id: str | None = None
    worksheet_name: str | None = None
    service_account_email: str | None = None
    last_export_at: datetime | None = None


class GoogleSheetsAppendResponse(BaseModel):
    appended: int
    errors: list[str]


async def _get_sheets_destination(
    db: AsyncSession, tenant_id: UUID
) -> GoogleSheetsDestination | None:
    return (
        await db.execute(
            select(GoogleSheetsDestination).where(
                GoogleSheetsDestination.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()


def _sa_email(blob: str) -> str | None:
    """Extract client_email from a stored SA JSON blob, or None on parse error.

    Used only to surface the email in status responses so the operator
    knows which account to share their Sheet with.
    """
    try:
        return parse_service_account(blob).get("client_email")
    except ValueError:
        return None


@router.post("/google-sheets", response_model=GoogleSheetsStatusResponse)
async def set_sheets_destination(
    body: GoogleSheetsConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> GoogleSheetsStatusResponse:
    # Validate the SA JSON shape up front so the user gets a 400 with a
    # clear reason instead of a 500 at the first export.
    try:
        parse_service_account(body.service_account_json)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    existing = await _get_sheets_destination(db, current_user.tenant_id)
    if existing:
        existing.service_account_json = body.service_account_json
        existing.spreadsheet_id = body.spreadsheet_id
        existing.worksheet_name = body.worksheet_name
    else:
        db.add(
            GoogleSheetsDestination(
                tenant_id=current_user.tenant_id,
                service_account_json=body.service_account_json,
                spreadsheet_id=body.spreadsheet_id,
                worksheet_name=body.worksheet_name,
            )
        )
    await db.commit()
    dest = await _get_sheets_destination(db, current_user.tenant_id)
    assert dest is not None
    log.info(
        "google_sheets_destination_set",
        tenant_id=str(current_user.tenant_id),
        spreadsheet_id=dest.spreadsheet_id,
    )
    return GoogleSheetsStatusResponse(
        connected=True,
        spreadsheet_id=dest.spreadsheet_id,
        worksheet_name=dest.worksheet_name,
        service_account_email=_sa_email(dest.service_account_json),
        last_export_at=dest.last_export_at,
    )


@router.get("/google-sheets", response_model=GoogleSheetsStatusResponse)
async def get_sheets_destination(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> GoogleSheetsStatusResponse:
    dest = await _get_sheets_destination(db, current_user.tenant_id)
    if dest is None:
        return GoogleSheetsStatusResponse(connected=False)
    return GoogleSheetsStatusResponse(
        connected=True,
        spreadsheet_id=dest.spreadsheet_id,
        worksheet_name=dest.worksheet_name,
        service_account_email=_sa_email(dest.service_account_json),
        last_export_at=dest.last_export_at,
    )


@router.delete("/google-sheets", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_sheets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    dest = await _get_sheets_destination(db, current_user.tenant_id)
    if dest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not connected"
        )
    await db.delete(dest)
    await db.commit()


@router.post(
    "/google-sheets/export/{job_id}",
    response_model=GoogleSheetsAppendResponse,
)
async def export_job_to_sheets(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> GoogleSheetsAppendResponse:
    dest = await _get_sheets_destination(db, current_user.tenant_id)
    if dest is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Sheets destination is not connected. POST credentials first.",
        )
    job = (
        await db.execute(
            select(Job).where(
                Job.id == job_id, Job.tenant_id == current_user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )

    entities = (
        await db.execute(
            select(Entity).where(
                Entity.job_id == job.id,
                Entity.duplicate_of.is_(None),
                Entity.review_status.not_in(("rejected", "duplicate")),
            )
        )
    ).scalars().all()

    # Send the header row only the first time we export to this destination
    # — after that, append-only semantics mean repeat headers would litter
    # the sheet.
    include_header = dest.last_export_at is None
    result = await append_entities(
        dest.service_account_json,
        dest.spreadsheet_id,
        dest.worksheet_name,
        list(entities),
        include_header=include_header,
    )
    if not result.errors:
        dest.last_export_at = datetime.now(UTC)
        await db.commit()
    log.info(
        "google_sheets_export_done",
        tenant_id=str(current_user.tenant_id),
        job_id=str(job_id),
        spreadsheet_id=dest.spreadsheet_id,
        appended=result.appended,
        errors=len(result.errors),
    )
    return GoogleSheetsAppendResponse(
        appended=result.appended, errors=result.errors
    )
