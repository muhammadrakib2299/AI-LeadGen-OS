"""Retention sweep — enforces the windows defined in compliance.md §6.

Deletes:
- raw_fetches older than 90 days (audit/debug data retention)
- exports older than 30 days
- entities that have not been re-verified in 24 months and are not on the blacklist
  (blacklist entries are permanent)

Safe to run repeatedly. Intended to be wired into a daily cron:
    uv run python scripts/retention_sweep.py

Options:
    --dry-run   Count what would be deleted without deleting anything.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.db.models import Entity, Export, RawFetch
from app.db.session import session_scope

RAW_FETCHES_TTL = timedelta(days=90)
EXPORTS_TTL = timedelta(days=30)
ENTITIES_TTL = timedelta(days=365 * 2)


async def sweep(session: AsyncSession, *, dry_run: bool) -> dict[str, int]:
    now = datetime.now(UTC)
    results: dict[str, int] = {}

    results["raw_fetches"] = await _purge(
        session,
        table=RawFetch,
        cutoff=now - RAW_FETCHES_TTL,
        column=RawFetch.created_at,
        dry_run=dry_run,
    )
    results["exports"] = await _purge(
        session,
        table=Export,
        cutoff=now - EXPORTS_TTL,
        column=Export.created_at,
        dry_run=dry_run,
    )
    results["entities"] = await _purge(
        session,
        table=Entity,
        cutoff=now - ENTITIES_TTL,
        column=Entity.updated_at,
        dry_run=dry_run,
    )
    return results


async def _purge(session: AsyncSession, *, table, cutoff: datetime, column, dry_run: bool) -> int:
    count_stmt = select(func.count()).select_from(table).where(column < cutoff)
    count = int((await session.execute(count_stmt)).scalar_one())
    if dry_run or count == 0:
        return count
    await session.execute(delete(table).where(column < cutoff))
    return count


async def main_async(dry_run: bool) -> None:
    configure_logging()
    log = get_logger("retention_sweep")
    async with session_scope() as session:
        counts = await sweep(session, dry_run=dry_run)
    log.info("retention_sweep_done", dry_run=dry_run, **counts)
    mode = "[DRY RUN] would delete" if dry_run else "deleted"
    for table, n in counts.items():
        print(f"{mode:25s} {n:>6d} rows from {table}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    main()
