"""Shared FastAPI dependencies."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_keys import hash_api_key
from app.core.security import decode_access_token
from app.db.models import ApiKey, User
from app.db.session import get_session

_AUTH_HEADER = "authorization"
_API_KEY_HEADER = "x-api-key"
_COOKIE_NAME = "leadgen_session"


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get(_AUTH_HEADER)
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    cookie = request.cookies.get(_COOKIE_NAME)
    return cookie or None


async def _user_from_api_key(plaintext: str, db: AsyncSession) -> User | None:
    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(plaintext))
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    # Hard expiry — set when the key was rotated. The rotation endpoint
    # gives the old key a 24h grace, then this check kicks in.
    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
        return None
    row.last_used_at = datetime.now(UTC)
    await db.flush()
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> User:
    # API keys take precedence: programmatic callers typically send both
    # headers only by accident, and X-API-Key is unambiguous.
    api_key = request.headers.get(_API_KEY_HEADER)
    if api_key:
        user = await _user_from_api_key(api_key.strip(), db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return user

    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user_id = UUID(sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
