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
from app.db.models import Blacklist
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
    email = str(payload.email).lower()

    existing = (
        await session.execute(select(Blacklist).where(Blacklist.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        log.info("opt_out_duplicate", email_domain=email.split("@")[1])
        return OptOutResponse(
            status="accepted",
            message="Already recorded. No further processing will occur.",
        )

    reason = (payload.reason or "").strip() or "gdpr_opt_out"
    session.add(Blacklist(email=email, reason=reason))
    try:
        await session.commit()
    except IntegrityError:
        # Race: concurrent request won the unique slot. Treat as success.
        await session.rollback()
    log.info("opt_out_recorded", email_domain=email.split("@")[1])
    return OptOutResponse(
        status="accepted",
        message="Your request has been recorded. No further processing will occur.",
    )
