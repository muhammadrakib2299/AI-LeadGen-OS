"""Blacklist admin API — operator-facing CRUD for GDPR erasures + manual blocks.

Unlike /privacy/opt-out which is for data subjects, this endpoint lets the
operator manage the blacklist directly (add a domain you never want to
contact, correct an operator mistake, etc.). Every DELETE is logged for
audit since compliance policy says the blacklist is durable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import Blacklist, User
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/blacklist", tags=["blacklist"])


class BlacklistCreateRequest(BaseModel):
    email: EmailStr | None = None
    domain: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _require_email_or_domain(self) -> BlacklistCreateRequest:
        if not self.email and not (self.domain and self.domain.strip()):
            raise ValueError("email or domain is required")
        if self.email and self.domain:
            raise ValueError("provide email or domain, not both")
        return self


class BlacklistResponse(BaseModel):
    id: UUID
    email: str | None
    domain: str | None
    reason: str | None
    created_at: datetime


class BlacklistListResponse(BaseModel):
    items: list[BlacklistResponse]
    total: int


def _to_response(row: Blacklist) -> BlacklistResponse:
    return BlacklistResponse(
        id=row.id,
        email=row.email,
        domain=row.domain,
        reason=row.reason,
        created_at=row.created_at,
    )


@router.post("", response_model=BlacklistResponse, status_code=201)
async def add_to_blacklist(
    payload: BlacklistCreateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BlacklistResponse:
    email = str(payload.email).lower() if payload.email else None
    domain = payload.domain.strip().lower() if payload.domain else None

    entry = Blacklist(
        tenant_id=current_user.tenant_id,
        email=email,
        domain=domain,
        reason=payload.reason,
    )
    session.add(entry)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        target = email or domain
        raise HTTPException(
            status_code=409, detail=f"'{target}' is already blacklisted"
        ) from None
    await session.refresh(entry)
    log.info(
        "blacklist_added",
        blacklist_id=str(entry.id),
        has_email=email is not None,
        has_domain=domain is not None,
    )
    return _to_response(entry)


@router.get("", response_model=BlacklistListResponse)
async def list_blacklist(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, description="substring match on email or domain"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BlacklistListResponse:
    filters = [Blacklist.tenant_id == current_user.tenant_id]
    if q:
        pattern = f"%{q.lower()}%"
        filters.append(
            or_(Blacklist.email.ilike(pattern), Blacklist.domain.ilike(pattern))
        )

    total_stmt = select(Blacklist).where(*filters)
    total = len((await session.execute(total_stmt)).scalars().all())

    stmt = (
        select(Blacklist)
        .where(*filters)
        .order_by(Blacklist.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return BlacklistListResponse(items=[_to_response(r) for r in rows], total=total)


@router.delete("/{entry_id}", status_code=204)
async def remove_from_blacklist(
    entry_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    stmt = select(Blacklist).where(
        Blacklist.id == entry_id,
        Blacklist.tenant_id == current_user.tenant_id,
    )
    entry = (await session.execute(stmt)).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="blacklist entry not found")

    log.warning(
        "blacklist_removed",
        blacklist_id=str(entry_id),
        had_email=entry.email is not None,
        had_domain=entry.domain is not None,
    )
    await session.delete(entry)
    await session.commit()
    return Response(status_code=204)
