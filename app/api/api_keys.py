"""API key management: create, list, revoke.

These endpoints themselves are gated by `get_current_user` at router include
time (main.py), so a caller must already have a JWT session (the dashboard)
or another API key to manage keys.

Keys are shown in plaintext exactly once — on creation. After that we only
have the hash, so a lost key must be revoked and replaced.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ApiKeyResponse(BaseModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


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
        )
        for r in rows
    ]
    return ApiKeyListResponse(items=items, total=len(items))


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
