"""Blacklist lookup — compliance.md §5. Checked before every entity write."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Blacklist


async def is_blacklisted(
    session: AsyncSession,
    *,
    email: str | None = None,
    domain: str | None = None,
    tenant_id: UUID | None = None,
) -> bool:
    """Return True if any of the given identifiers is blacklisted for this tenant.

    When `tenant_id` is supplied (normal path from JobRunner), the check is
    scoped — one tenant's blacklist doesn't suppress another's leads. For
    legacy callers without a tenant, a global match is still considered a
    hit; this keeps us safe during the rollout.
    """
    if not email and not domain:
        return False

    conditions = []
    if email:
        conditions.append(func.lower(Blacklist.email) == email.lower())
    if domain:
        conditions.append(func.lower(Blacklist.domain) == domain.lower())

    stmt = select(Blacklist.id).where(or_(*conditions))
    if tenant_id is not None:
        stmt = stmt.where(Blacklist.tenant_id == tenant_id)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None
