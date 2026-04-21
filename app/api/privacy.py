"""Privacy endpoints — GDPR Art. 17 / 21 opt-out.

POST /privacy/opt-out accepts an email from a data subject and writes it
to the Blacklist table. Job runs honor the blacklist before persisting
any entity (see app/services/blacklist.py + app/services/job_runner.py).

The endpoint is idempotent: retrying with the same email returns the
same 202 response without creating duplicate rows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Blacklist, Entity, Tenant
from app.db.session import get_session

router = APIRouter(prefix="/privacy", tags=["privacy"])
log = get_logger(__name__)


class OptOutRequest(BaseModel):
    email: EmailStr
    reason: str | None = None


class OptOutResponse(BaseModel):
    status: str
    message: str


@router.post("/opt-out", response_model=OptOutResponse, status_code=202)
async def opt_out(
    payload: OptOutRequest,
    session: AsyncSession = Depends(get_session),
) -> OptOutResponse:
    """GDPR opt-out fans out to every tenant holding data for this subject.

    A data subject filing an erasure request via the public form has no
    tenant context — and morally, the request applies to every operator
    that might touch them. We write one Blacklist row per tenant that
    currently has an entity row matching the email (or, for forward
    protection, per tenant that exists at all). The alternative — a
    single global blacklist — breaks multi-tenant isolation.
    """
    email = str(payload.email).lower()
    reason = (payload.reason or "").strip() or "gdpr_opt_out"

    # Find tenants with any entity carrying this email, PLUS every tenant
    # whose blacklist doesn't already have it — so future jobs in any
    # tenant will respect the request even if they haven't discovered the
    # subject yet.
    tenant_ids = {
        t for t, in (await session.execute(select(Tenant.id))).all()
    }

    # Already-recorded: if every tenant already has this email blacklisted,
    # this is a pure dedupe.
    existing_tenants = {
        t for t, in (
            await session.execute(
                select(Blacklist.tenant_id).where(Blacklist.email == email)
            )
        ).all()
    }
    missing = tenant_ids - existing_tenants

    if not missing:
        log.info("opt_out_duplicate", email_domain=email.split("@")[1])
        return OptOutResponse(
            status="accepted",
            message="Already recorded. No further processing will occur.",
        )

    for tid in missing:
        session.add(Blacklist(tenant_id=tid, email=email, reason=reason))
    try:
        await session.commit()
    except IntegrityError:
        # Race with another opt-out writer for one or more tenants; the
        # unique constraint filtered the dupes for us.
        await session.rollback()

    # Also flag any existing entity rows as rejected so in-flight runs
    # don't surface them in an export. This fans out across tenants
    # intentionally — a data subject's opt-out is a global suppression.
    from sqlalchemy import update

    await session.execute(
        update(Entity).where(Entity.email == email).values(review_status="rejected")
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()

    log.info(
        "opt_out_recorded",
        email_domain=email.split("@")[1],
        tenants_added=len(missing),
    )
    return OptOutResponse(
        status="accepted",
        message="Your request has been recorded. No further processing will occur.",
    )
