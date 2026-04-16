"""Blacklist lookup — compliance.md §5. Checked before every entity write."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blacklist


async def is_blacklisted(
    session: AsyncSession, *, email: str | None = None, domain: str | None = None
) -> bool:
    """Return True if any of the given identifiers is in the blacklist."""
    if not email and not domain:
        return False

    conditions = []
    if email:
        conditions.append(func.lower(Blacklist.email) == email.lower())
    if domain:
        conditions.append(func.lower(Blacklist.domain) == domain.lower())

    stmt = select(Blacklist.id).where(or_(*conditions)).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None
