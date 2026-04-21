"""Re-verify aged entity records.

Freshness rots. A website that was live six months ago may be parked, an
email MX may have moved, a phone number may have been recycled. This module
runs a cheap re-check pass over the oldest N entities:

- website  → url_liveness HEAD probe
- email    → syntax + MX re-verify
- phone    → libphonenumber parse (deterministic, no network)

Fields that still verify get their `field_sources.<field>.fetched_at`
bumped. Fields that fail get their confidence multiplied by the verification
status's `confidence_boost`, and verification metadata updated. The entity's
`updated_at` always moves forward when we touch it, which doubles as the
"last re-verified" signal — the retention sweep and the freshness badges
in the UI both read it.

Intended callers:
- `scripts/reverify_aged.py`  — daily cron
- `POST /reverify`            — on-demand trigger from the dashboard

This is intentionally a re-verification pass, not re-discovery. If the
homepage moved or the business renamed itself, only a fresh discovery job
will catch that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Entity
from app.services.email_verify import verify_email
from app.services.phone_verify import verify_phone
from app.services.url_liveness import check_url_liveness

log = get_logger(__name__)

DEFAULT_MAX_AGE_DAYS = 90
DEFAULT_BATCH_LIMIT = 50


@dataclass(slots=True)
class ReverifyResult:
    scanned: int = 0
    websites_checked: int = 0
    websites_dead: int = 0
    emails_checked: int = 0
    emails_invalid: int = 0
    phones_checked: int = 0
    phones_invalid: int = 0
    errors: list[str] = field(default_factory=list)


async def reverify_stale_entities(
    session: AsyncSession,
    http: httpx.AsyncClient,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    limit: int = DEFAULT_BATCH_LIMIT,
) -> ReverifyResult:
    """Re-verify the `limit` oldest non-duplicate entities older than `max_age_days`.

    Never raises — per-entity failures are logged to `ReverifyResult.errors`
    and execution continues to the next row. This is intended to run
    unattended on a cron; a single flaky website should not poison the batch.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

    stmt = (
        select(Entity)
        .where(Entity.updated_at < cutoff, Entity.duplicate_of.is_(None))
        .order_by(Entity.updated_at.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()

    result = ReverifyResult()
    for entity in rows:
        result.scanned += 1
        try:
            await _reverify_one(entity, http, result)
            entity.updated_at = datetime.now(UTC)
            await session.flush()
        except Exception as exc:
            result.errors.append(f"{entity.id}: {type(exc).__name__}: {exc}")
            log.warning(
                "reverify_entity_failed",
                entity_id=str(entity.id),
                error=str(exc),
            )

    log.info(
        "reverify_batch_done",
        scanned=result.scanned,
        websites_dead=result.websites_dead,
        emails_invalid=result.emails_invalid,
        phones_invalid=result.phones_invalid,
    )
    return result


async def _reverify_one(
    entity: Entity, http: httpx.AsyncClient, result: ReverifyResult
) -> None:
    sources: dict[str, Any] = dict(entity.field_sources or {})
    now_iso = datetime.now(UTC).isoformat()

    if entity.website:
        result.websites_checked += 1
        liveness = await check_url_liveness(http, entity.website)
        if liveness.status == "dead":
            result.websites_dead += 1
        sources["website"] = _merge_website_source(
            sources.get("website"), liveness, now_iso
        )

    if entity.email:
        result.emails_checked += 1
        email_v = await verify_email(entity.email)
        if email_v.status in ("invalid_syntax", "no_mx"):
            result.emails_invalid += 1
        sources["email"] = _merge_email_source(sources.get("email"), email_v, now_iso)

    if entity.phone:
        result.phones_checked += 1
        phone_v = verify_phone(entity.phone, region=entity.country)
        if phone_v.status in ("invalid", "unparseable"):
            result.phones_invalid += 1
        sources["phone"] = _merge_phone_source(sources.get("phone"), phone_v, now_iso)

    entity.field_sources = sources


# Status → confidence multiplier maps, scoped per field because statuses like
# "unreachable" have different semantics for a URL vs. an email MX lookup.
# Keep these in sync with the dataclasses' `confidence_boost` properties.
_WEBSITE_BOOSTS: dict[str, float] = {
    "alive": 1.02,
    "dead": 0.3,
    "unreachable": 0.6,
    "unknown": 1.0,
}
_EMAIL_BOOSTS: dict[str, float] = {
    "valid": 1.05,
    "invalid_syntax": 0.0,
    "no_mx": 0.4,
    "unreachable": 0.9,
}
_PHONE_BOOSTS: dict[str, float] = {
    "valid": 1.05,
    "possible": 0.75,
    "invalid": 0.25,
    "unparseable": 0.0,
}


def _merge_website_source(
    existing: dict[str, Any] | None, liveness: Any, now_iso: str
) -> dict[str, Any]:
    out: dict[str, Any] = dict(existing or {})
    base = float(out.get("confidence", 0.9))
    # Strip the prior liveness boost before re-applying the new one so
    # repeated re-verifications don't compound.
    prior_boost = _boost_from_payload(out.get("liveness"), _WEBSITE_BOOSTS)
    base_no_prior = base / prior_boost if prior_boost else base
    out["fetched_at"] = now_iso
    out["confidence"] = round(
        max(0.0, min(1.0, base_no_prior * liveness.confidence_boost)), 3
    )
    out["liveness"] = {
        "status": liveness.status,
        "http_status": liveness.http_status,
    }
    return out


def _merge_email_source(
    existing: dict[str, Any] | None, verification: Any, now_iso: str
) -> dict[str, Any]:
    out: dict[str, Any] = dict(existing or {})
    base = float(out.get("confidence", 0.8))
    prior_boost = _boost_from_payload(out.get("verification"), _EMAIL_BOOSTS)
    base_no_prior = base / prior_boost if prior_boost else base
    out["fetched_at"] = now_iso
    out["confidence"] = round(
        max(0.0, min(1.0, base_no_prior * verification.confidence_boost)), 3
    )
    out["verification"] = {
        "status": verification.status,
        "mx_host": verification.mx_host,
    }
    return out


def _merge_phone_source(
    existing: dict[str, Any] | None, verification: Any, now_iso: str
) -> dict[str, Any]:
    out: dict[str, Any] = dict(existing or {})
    base = float(out.get("confidence", 0.85))
    prior_boost = _boost_from_payload(out.get("verification"), _PHONE_BOOSTS)
    base_no_prior = base / prior_boost if prior_boost else base
    out["fetched_at"] = now_iso
    out["confidence"] = round(
        max(0.0, min(1.0, base_no_prior * verification.confidence_boost)), 3
    )
    out["verification"] = {
        "status": verification.status,
        "kind": verification.kind,
        "region": verification.region,
    }
    return out


def _boost_from_payload(payload: Any, boost_map: dict[str, float]) -> float:
    if not isinstance(payload, dict):
        return 1.0
    status = payload.get("status")
    if not isinstance(status, str):
        return 1.0
    return boost_map.get(status, 1.0)
