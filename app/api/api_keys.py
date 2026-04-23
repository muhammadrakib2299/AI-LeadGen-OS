"""API key management: create, list, rotate, revoke.

These endpoints themselves are gated by `get_current_user` at router include
time (main.py), so a caller must already have a JWT session (the dashboard)
or another API key to manage keys.

Keys are shown in plaintext exactly once — on creation. After that we only
have the hash, so a lost key must be revoked and replaced.

Rotation issues a brand-new key and stamps the old one with a 24-hour
expires_at, so deployed callers can swap to the new key without an outage.
After 24h the old key auto-rejects at the auth layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.api_keys import generate_api_key
from app.core.logging import get_logger
from app.db.models import ApiKey, User
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/api-keys", tags=["api-keys"])

ROTATION_GRACE = timedelta(hours=24)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ApiKeyResponse(BaseModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None = None
    rotated_to_id: UUID | None = None


class ApiKeyCreateResponse(ApiKeyResponse):
    # Plaintext key, returned exactly once. Clients must store it on their side.
    key: str


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyResponse]
    total: int


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: ApiKeyCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiKeyCreateResponse:
    plaintext, prefix, key_hash = generate_api_key()
    row = ApiKey(
        user_id=current_user.id,
        name=body.name.strip(),
        prefix=prefix,
        key_hash=key_hash,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    log.info("api_key_created", user_id=str(current_user.id), key_id=str(row.id))
    return ApiKeyCreateResponse(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        expires_at=row.expires_at,
        rotated_to_id=row.rotated_to_id,
        key=plaintext,
    )


@router.get("", response_model=ApiKeyListResponse)
async def list_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiKeyListResponse:
    rows = (
        await db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == current_user.id)
            .order_by(ApiKey.created_at.desc())
        )
    ).scalars().all()
    items = [
        ApiKeyResponse(
            id=r.id,
            name=r.name,
            prefix=r.prefix,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            revoked_at=r.revoked_at,
            expires_at=r.expires_at,
            rotated_to_id=r.rotated_to_id,
        )
        for r in rows
    ]
    return ApiKeyListResponse(items=items, total=len(items))


@router.post(
    "/{key_id}/rotate",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_key(
    key_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiKeyCreateResponse:
    """Issue a new key and stamp the old one with a 24h expiry.

    Both keys authenticate during the grace window so a deployed caller
    can flip its config without an outage. After the grace, the old key
    is rejected at the auth layer (see app/api/deps.py). Already-revoked
    keys can't be rotated — revoke is terminal.
    """
    old = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.id == key_id, ApiKey.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if old is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    if old.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Key is revoked; create a new one instead.",
        )
    if old.rotated_to_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Key has already been rotated.",
        )

    plaintext, prefix, key_hash = generate_api_key()
    new = ApiKey(
        user_id=current_user.id,
        name=old.name,
        prefix=prefix,
        key_hash=key_hash,
    )
    db.add(new)
    await db.flush()  # populate new.id for the FK on the old row

    old.expires_at = datetime.now(UTC) + ROTATION_GRACE
    old.rotated_to_id = new.id
    await db.commit()
    await db.refresh(new)
    log.info(
        "api_key_rotated",
        user_id=str(current_user.id),
        old_key_id=str(old.id),
        new_key_id=str(new.id),
    )
    return ApiKeyCreateResponse(
        id=new.id,
        name=new.name,
        prefix=new.prefix,
        created_at=new.created_at,
        last_used_at=new.last_used_at,
        revoked_at=new.revoked_at,
        expires_at=new.expires_at,
        rotated_to_id=new.rotated_to_id,
        key=plaintext,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.id == key_id, ApiKey.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await db.commit()
    log.info("api_key_revoked", user_id=str(current_user.id), key_id=str(row.id))
