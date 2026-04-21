"""Password hashing + JWT encode/decode.

Single-tenant Phase 4 auth. Multi-tenant isolation (scoping rows per user) is
deferred to Phase 5. bcrypt is used directly (not via passlib) because passlib
upstream is unmaintained against bcrypt 4.x.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
import jwt

from app.core.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash (should never happen for rows we wrote).
        return False


def create_access_token(user_id: UUID, email: str) -> tuple[str, datetime]:
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.jwt_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
