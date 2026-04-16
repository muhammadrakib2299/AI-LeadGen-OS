"""Fuzzy dedupe inside a single job's entity set.

Uses Postgres pg_trgm `similarity()` on normalized name + same-city context.
Exact-domain duplicates are already blocked at insert by the
`uq_entity_job_domain` unique constraint; this service catches the harder
cases (accent differences, trailing company suffixes, etc.).

Winner policy: higher quality_score wins; ties go to older row (lower
created_at). Losers are marked with `review_status = "duplicate"` and their
`duplicate_of` points at the winner. No rows are deleted — audit trail stays
intact and a human can still inspect the duplicates via /review.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Entity

log = get_logger(__name__)


DEFAULT_SIMILARITY_THRESHOLD = 0.7


_PAIR_QUERY = text(
    """
    SELECT
        e1.id AS id1,
        e2.id AS id2,
        similarity(lower(e1.name), lower(e2.name)) AS sim,
        COALESCE(e1.quality_score, 0) AS q1,
        COALESCE(e2.quality_score, 0) AS q2,
        e1.created_at AS c1,
        e2.created_at AS c2
    FROM entities e1
    JOIN entities e2
      ON e2.job_id = e1.job_id
     AND e2.id > e1.id
     AND e2.review_status <> 'duplicate'
    WHERE e1.job_id = :job_id
      AND e1.review_status <> 'duplicate'
      AND (
            COALESCE(lower(e1.city), '') = COALESCE(lower(e2.city), '')
         OR COALESCE(e1.city, '') = ''
         OR COALESCE(e2.city, '') = ''
      )
      AND similarity(lower(e1.name), lower(e2.name)) >= :threshold
    ORDER BY sim DESC
    """
)


async def dedupe_job(
    session: AsyncSession,
    job_id: UUID,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> int:
    """Mark fuzzy duplicates within one job. Returns number of rows collapsed."""
    result = await session.execute(_PAIR_QUERY, {"job_id": str(job_id), "threshold": threshold})
    pairs = result.fetchall()

    already_merged: set[UUID] = set()
    merged = 0

    for row in pairs:
        id1: UUID = row.id1
        id2: UUID = row.id2
        if id1 in already_merged or id2 in already_merged:
            continue

        if row.q1 > row.q2 or (row.q1 == row.q2 and row.c1 <= row.c2):
            winner_id, loser_id = id1, id2
        else:
            winner_id, loser_id = id2, id1

        await session.execute(
            update(Entity)
            .where(Entity.id == loser_id)
            .values(review_status="duplicate", duplicate_of=winner_id)
        )
        already_merged.add(loser_id)
        merged += 1
        log.info(
            "dedupe_merged",
            job_id=str(job_id),
            winner_id=str(winner_id),
            loser_id=str(loser_id),
            similarity=float(row.sim),
        )

    await session.flush()
    return merged
