"""Auth endpoints: register, login, me.

Single-tenant for Phase 4. Registration is open; in a single-operator deploy
this is acceptable because the operator controls DNS and can lock the endpoint
at the edge. Multi-tenant invite flow is Phase 5.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "leadgen_session"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: UUID
    email: str
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth2 token-type literal, not a secret
    expires_at: datetime
    user: UserResponse


def _set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    settings = get_settings()
    secure = settings.environment != "dev"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.jwt_ttl_seconds,
        path="/",
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> TokenResponse:
    email = body.email.lower().strip()
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    await db.refresh(user)
    token, expires_at = create_access_token(user.id, user.email)
    _set_session_cookie(response, token, expires_at)
    log.info("user_registered", user_id=str(user.id), email=email)
    return TokenResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserResponse(id=user.id, email=user.email, is_active=user.is_active),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> TokenResponse:
    email = body.email.lower().strip()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        # Same response for unknown email and bad password → no enumeration.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        )
    token, expires_at = create_access_token(user.id, user.email)
    _set_session_cookie(response, token, expires_at)
    log.info("user_login", user_id=str(user.id), email=email)
    return TokenResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserResponse(id=user.id, email=user.email, is_active=user.is_active),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=current_user.id, email=current_user.email, is_active=current_user.is_active
    )
