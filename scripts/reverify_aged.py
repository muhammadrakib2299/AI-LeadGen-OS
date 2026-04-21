"""Re-verify aged entity records.

Daily cron companion to `retention_sweep.py`. Picks the oldest N entities
whose `updated_at` is more than `--max-age-days` days ago and re-checks
website liveness, email MX, and phone parse. See `app/services/reverify.py`
for the full policy.

Usage:
    uv run python scripts/reverify_aged.py
    uv run python scripts/reverify_aged.py --max-age-days 30 --limit 200
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import session_scope
from app.services.reverify import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_MAX_AGE_DAYS,
    reverify_stale_entities,
)


async def main_async(max_age_days: int, limit: int) -> None:
    configure_logging()
    log = get_logger("reverify_aged")
    settings = get_settings()

    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": settings.default_user_agent},
    ) as http, session_scope() as session:
        result = await reverify_stale_entities(
            session, http, max_age_days=max_age_days, limit=limit
        )

    log.info(
        "reverify_script_done",
        scanned=result.scanned,
        websites_dead=result.websites_dead,
        emails_invalid=result.emails_invalid,
        phones_invalid=result.phones_invalid,
        errors=len(result.errors),
    )
    print(
        f"reverified {result.scanned} entities "
        f"({result.websites_dead} dead websites, "
        f"{result.emails_invalid} invalid emails, "
        f"{result.phones_invalid} invalid phones, "
        f"{len(result.errors)} errors)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Only re-verify entities whose updated_at is older than this (default: 90).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BATCH_LIMIT,
        help="Maximum number of entities to re-verify in this run (default: 50).",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.max_age_days, args.limit))


if __name__ == "__main__":
    main()
