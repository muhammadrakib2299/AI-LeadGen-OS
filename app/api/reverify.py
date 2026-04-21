"""POST /reverify — on-demand trigger for re-verifying aged entities.

Gated behind operator auth. Runs the same logic as scripts/reverify_aged.py
but synchronously inside the request, so the dashboard can surface the
counts directly. For large batches (limit > ~100) an operator should still
prefer the cron script.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session
from app.services.reverify import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_MAX_AGE_DAYS,
    reverify_stale_entities,
)

log = get_logger(__name__)
router = APIRouter(prefix="/reverify", tags=["reverify"])


class ReverifyRequest(BaseModel):
    max_age_days: int = Field(default=DEFAULT_MAX_AGE_DAYS, ge=1, le=365)
    limit: int = Field(default=DEFAULT_BATCH_LIMIT, ge=1, le=500)


class ReverifyResponse(BaseModel):
    scanned: int
    websites_checked: int
    websites_dead: int
    emails_checked: int
    emails_invalid: int
    phones_checked: int
    phones_invalid: int
    errors: list[str]


@router.post("", response_model=ReverifyResponse)
async def run_reverify(
    body: ReverifyRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> ReverifyResponse:
    params = body or ReverifyRequest()
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": settings.default_user_agent},
    ) as http:
        result = await reverify_stale_entities(
            session,
            http,
            max_age_days=params.max_age_days,
            limit=params.limit,
        )
    await session.commit()
    return ReverifyResponse(
        scanned=result.scanned,
        websites_checked=result.websites_checked,
        websites_dead=result.websites_dead,
        emails_checked=result.emails_checked,
        emails_invalid=result.emails_invalid,
        phones_checked=result.phones_checked,
        phones_invalid=result.phones_invalid,
        errors=result.errors,
    )
