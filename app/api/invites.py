"""Tenant invitation endpoints.

- POST   /tenants/invites          (auth) — create an invite; returns
                                    plaintext token in the response so the
                                    operator can paste a join link to the
                                    invitee. Token is shown exactly once.
- GET    /tenants/invites          (auth) — list outstanding invites
- DELETE /tenants/invites/{id}     (auth) — revoke
- POST   /auth/accept-invite       (public) — consume a token + password
                                    to create a User joined to the tenant
"""

from __future__ import annotations

import hashlib
import secrets as _secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import COOKIE_NAME, TokenResponse, UserResponse, _set_session_cookie
from app.api.deps import get_current_user
from app.core.config import get_settings  # noqa: F401 — kept for parity
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password
from app.db.models import TenantInvite, User
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/tenants/invites", tags=["invites"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])

INVITE_TTL_DAYS = 14


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class InviteCreateRequest(BaseModel):
    email: EmailStr


class InviteResponse(BaseModel):
    id: UUID
    email: str
    accepted_at: datetime | None
    expires_at: datetime
    created_at: datetime


class InviteCreateResponse(InviteResponse):
    # Plaintext invite token, shown exactly once. The frontend embeds this
    # in a join URL and emails / messages it to the invitee.
    token: str


class InviteListResponse(BaseModel):
    items: list[InviteResponse]
    total: int


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=10, max_length=128)
    password: str = Field(min_length=8, max_length=128)


def _to_response(inv: TenantInvite) -> InviteResponse:
    return InviteResponse(
        id=inv.id,
        email=inv.email,
        accepted_at=inv.accepted_at,
        expires_at=inv.expires_at,
        created_at=inv.created_at,
    )


@router.post("", response_model=InviteCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> InviteCreateResponse:
    token = _secrets.token_urlsafe(32)
    invite = TenantInvite(
        tenant_id=current_user.tenant_id,
        email=str(body.email).lower().strip(),
        token_hash=_hash_token(token),
        invited_by_user_id=current_user.id,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    log.info("invite_created", invite_id=str(invite.id), tenant_id=str(invite.tenant_id))
    base = _to_response(invite)
    return InviteCreateResponse(**base.model_dump(), token=token)


@router.get("", response_model=InviteListResponse)
async def list_invites(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> InviteListResponse:
    rows = (
        await db.execute(
            select(TenantInvite)
            .where(TenantInvite.tenant_id == current_user.tenant_id)
            .order_by(TenantInvite.created_at.desc())
        )
    ).scalars().all()
    return InviteListResponse(items=[_to_response(r) for r in rows], total=len(rows))


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    inv = (
        await db.execute(
            select(TenantInvite).where(
                TenantInvite.id == invite_id,
                TenantInvite.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
    await db.delete(inv)
    await db.commit()


@auth_router.post(
    "/accept-invite",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_invite(
    body: AcceptInviteRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Consume an invite token: create a User under the inviting tenant.

    Public endpoint — the bearer of a valid token IS the authentication.
    Tokens are single-use (we set accepted_at on success) and expire after
    INVITE_TTL_DAYS. We use the invite's stored email rather than letting
    the caller pick one — that way the inviter stays in control of who
    actually joins.
    """
    invite = (
        await db.execute(
            select(TenantInvite).where(TenantInvite.token_hash == _hash_token(body.token))
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
    if invite.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="invite already accepted"
        )
    if invite.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite expired")

    user = User(
        email=invite.email,
        password_hash=hash_password(body.password),
        tenant_id=invite.tenant_id,
    )
    db.add(user)
    invite.accepted_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as exc:
        # The email is already registered — likely the invitee bypassed
        # the invite link and registered directly. Treat as a conflict.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    await db.refresh(user)
    token, expires_at = create_access_token(user.id, user.email)
    _set_session_cookie(response, token, expires_at)
    log.info(
        "invite_accepted",
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        invite_id=str(invite.id),
    )
    return TokenResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserResponse(
            id=user.id,
            email=user.email,
            is_active=user.is_active,
            tenant_id=user.tenant_id,
        ),
    )


# Re-export the cookie name for tests that want to clear it.
__all__ = ["auth_router", "router", "COOKIE_NAME"]
