"""AI Ask Mode — natural-language questions over the tenant's enriched leads.

v1 is NL → structured filter, not embedding-based RAG. The LLM emits a
JSON spec describing which entities to retrieve; we run the actual SQL
ourselves and return the matching rows alongside a one-line summary.

Why not vector RAG yet?
- Embedding every entity write needs an embeddings provider (we don't
  have one wired) plus a pgvector migration. v2.
- Most operator questions are "show me leads matching X" — that's a
  filter, not a similarity search. The structured-filter approach
  answers those exactly and cheaply.

The LLM never touches PII. We send it the question + the schema only;
SQL execution and row return happen entirely in our process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Entity, Job
from app.services.llm import LLMClient

log = get_logger(__name__)

ALLOWED_LEAD_STATUSES: set[str] = {
    "new",
    "contacted",
    "responded",
    "converted",
    "lost",
}
ALLOWED_SORT_BY: set[str] = {"quality_score", "created_at"}
DEFAULT_LIMIT = 25
MAX_LIMIT = 200


SYSTEM_PROMPT = """You translate operator questions about a CRM-style leads database into a JSON filter spec.

The database has these entity fields available for filtering:
- city: free-text city name (case-insensitive substring match)
- country: ISO 3166-1 alpha-2 (FR, DE, GB, IT, ES, NL, US, ...). "UK" must be normalized to "GB".
- category: free-text business category (case-insensitive substring match)
- lead_status: one of "new" | "contacted" | "responded" | "converted" | "lost"
- min_quality_score: integer 0-100. Set when the question implies "high-quality" / "good" / "best".
- has_email: true when the question implies emails are required ("with emails", "verified", "reachable").

Return strictly a single JSON object with this shape:
{
  "filter": {
    "city": "<string or null>",
    "country": "<2-letter ISO code or null>",
    "category": "<string or null>",
    "lead_status": "<one of the allowed values or null>",
    "min_quality_score": <int 0-100 or null>,
    "has_email": <true | false | null>
  },
  "sort_by": "quality_score" | "created_at",
  "limit": <int 1-200>,
  "summary": "<one short sentence describing what was searched, in plain English>"
}

Rules:
- Omit a field (or set null) when the question doesn't mention it. Do not invent constraints.
- "best", "top", "high-quality" → set min_quality_score=70 unless a number is given.
- "with emails", "verified", "reachable" → set has_email=true.
- "haven't been contacted", "fresh", "untouched" → set lead_status="new".
- "responded", "replied" → set lead_status="responded".
- Default sort_by is "quality_score" unless the question implies recency ("newest", "recent" → "created_at").
- Default limit is 25; cap at 200.
- Output ONLY the JSON object — no prose, no code fences.
"""


@dataclass(slots=True)
class AskFilter:
    city: str | None = None
    country: str | None = None
    category: str | None = None
    lead_status: str | None = None
    min_quality_score: int | None = None
    has_email: bool | None = None


@dataclass(slots=True)
class AskSpec:
    filter: AskFilter
    sort_by: str
    limit: int
    summary: str


@dataclass(slots=True)
class AskResult:
    spec: AskSpec
    rows: list[Entity]


def coerce_spec(raw: dict[str, Any]) -> AskSpec:
    """Turn a (possibly noisy) LLM JSON response into a safe, normalized AskSpec.

    Anything we don't recognize is dropped — the SQL query is built only
    from whitelisted columns and operators, never from raw LLM strings.
    """
    raw_filter = raw.get("filter") or {}
    if not isinstance(raw_filter, dict):
        raw_filter = {}

    country = raw_filter.get("country")
    if isinstance(country, str):
        country = country.strip().upper()
        if country == "UK":
            country = "GB"
        if len(country) != 2:
            country = None
    else:
        country = None

    lead_status = raw_filter.get("lead_status")
    if not (isinstance(lead_status, str) and lead_status in ALLOWED_LEAD_STATUSES):
        lead_status = None

    min_q = raw_filter.get("min_quality_score")
    if isinstance(min_q, (int, float)) and 0 <= int(min_q) <= 100:
        min_quality_score: int | None = int(min_q)
    else:
        min_quality_score = None

    has_email = raw_filter.get("has_email")
    if not isinstance(has_email, bool):
        has_email = None

    sort_by = raw.get("sort_by")
    if sort_by not in ALLOWED_SORT_BY:
        sort_by = "quality_score"

    limit_raw = raw.get("limit")
    try:
        limit = int(limit_raw) if limit_raw is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(MAX_LIMIT, limit))

    summary = raw.get("summary") or ""
    if not isinstance(summary, str):
        summary = ""

    return AskSpec(
        filter=AskFilter(
            city=_str_or_none(raw_filter.get("city")),
            country=country,
            category=_str_or_none(raw_filter.get("category")),
            lead_status=lead_status,
            min_quality_score=min_quality_score,
            has_email=has_email,
        ),
        sort_by=sort_by,
        limit=limit,
        summary=summary.strip()[:500],
    )


def _str_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


async def execute_spec(
    db: AsyncSession, tenant_id: UUID, spec: AskSpec
) -> list[Entity]:
    """Run the spec's filters as a tenant-scoped SELECT against entities.

    Tenant scoping joins through Job — the same path every other read
    endpoint uses, so a malformed spec can't reach foreign data.
    """
    stmt = (
        select(Entity)
        .join(Job, Entity.job_id == Job.id)
        .where(
            Job.tenant_id == tenant_id,
            Entity.duplicate_of.is_(None),
            Entity.review_status.not_in(("rejected", "duplicate")),
        )
    )

    f = spec.filter
    if f.city:
        stmt = stmt.where(Entity.city.ilike(f"%{_escape_like(f.city)}%"))
    if f.country:
        stmt = stmt.where(Entity.country == f.country)
    if f.category:
        stmt = stmt.where(Entity.category.ilike(f"%{_escape_like(f.category)}%"))
    if f.lead_status:
        stmt = stmt.where(Entity.lead_status == f.lead_status)
    if f.min_quality_score is not None:
        stmt = stmt.where(Entity.quality_score >= f.min_quality_score)
    if f.has_email is True:
        stmt = stmt.where(Entity.email.is_not(None))
    elif f.has_email is False:
        stmt = stmt.where(Entity.email.is_(None))

    if spec.sort_by == "created_at":
        stmt = stmt.order_by(Entity.created_at.desc())
    else:
        # Nulls-last by default would need dialect-specific syntax; instead
        # keep it simple: high quality_score wins, NULLs sort last on most
        # backends including Postgres.
        stmt = stmt.order_by(Entity.quality_score.desc().nulls_last())

    stmt = stmt.limit(spec.limit)
    return list((await db.execute(stmt)).scalars().all())


def _escape_like(value: str) -> str:
    """Neutralize LIKE wildcards so user-controlled strings can't match more
    than they intend."""
    return re.sub(r"([%_\\])", r"\\\1", value)


async def ask(
    llm: LLMClient,
    db: AsyncSession,
    tenant_id: UUID,
    question: str,
) -> AskResult:
    raw = await llm.complete_json(
        system=SYSTEM_PROMPT,
        user=question,
        max_tokens=512,
    )
    spec = coerce_spec(raw)
    rows = await execute_spec(db, tenant_id, spec)
    log.info(
        "ask_done",
        tenant_id=str(tenant_id),
        question=question[:120],
        match_count=len(rows),
        sort_by=spec.sort_by,
    )
    return AskResult(spec=spec, rows=rows)
