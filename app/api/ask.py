"""POST /ask — natural-language questions over the tenant's leads.

Two modes:
- POST /ask         — v1 structured-filter mode. LLM parses question to
                      a SQL filter spec; rows never round-trip through
                      the model.
- POST /ask/similar — v2 vector similarity mode. Question is embedded
                      and matched against `entities.embedding` by cosine
                      distance. Tenant-scoped through Job.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Entity, Job, User
from app.db.session import get_session
from app.services.ask import AskFilter, AskSpec, ask
from app.services.embeddings import OpenAIEmbeddings
from app.services.llm import AnthropicClient

log = get_logger(__name__)
router = APIRouter(prefix="/ask", tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AskFilterResponse(BaseModel):
    city: str | None = None
    country: str | None = None
    category: str | None = None
    lead_status: str | None = None
    min_quality_score: int | None = None
    has_email: bool | None = None


class AskRowResponse(BaseModel):
    id: UUID
    job_id: UUID
    name: str
    email: str | None = None
    website: str | None = None
    city: str | None = None
    country: str | None = None
    category: str | None = None
    quality_score: int | None = None
    lead_status: str


class AskResponse(BaseModel):
    summary: str
    filter: AskFilterResponse
    sort_by: str
    limit: int
    match_count: int
    rows: list[AskRowResponse]


def _filter_response(f: AskFilter) -> AskFilterResponse:
    return AskFilterResponse(
        city=f.city,
        country=f.country,
        category=f.category,
        lead_status=f.lead_status,
        min_quality_score=f.min_quality_score,
        has_email=f.has_email,
    )


def _row_response(e: Entity) -> AskRowResponse:
    return AskRowResponse(
        id=e.id,
        job_id=e.job_id,
        name=e.name,
        email=e.email,
        website=e.website,
        city=e.city,
        country=e.country,
        category=e.category,
        quality_score=e.quality_score,
        lead_status=e.lead_status,
    )


def _build_response(spec: AskSpec, rows: list[Entity]) -> AskResponse:
    return AskResponse(
        summary=spec.summary,
        filter=_filter_response(spec.filter),
        sort_by=spec.sort_by,
        limit=spec.limit,
        match_count=len(rows),
        rows=[_row_response(e) for e in rows],
    )


@router.post("", response_model=AskResponse)
async def ask_endpoint(
    body: AskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> AskResponse:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ANTHROPIC_API_KEY is not configured on the server.",
        )

    llm = AnthropicClient(api_key=settings.anthropic_api_key)
    result = await ask(llm, db, current_user.tenant_id, body.question)
    return _build_response(result.spec, result.rows)


# ── Ask Mode v2: vector similarity ────────────────────────────────────


class SimilarRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)


class SimilarRow(AskRowResponse):
    similarity: float  # 0.0–1.0, higher = closer match


class SimilarResponse(BaseModel):
    question: str
    match_count: int
    rows: list[SimilarRow]


@router.post("/similar", response_model=SimilarResponse)
async def ask_similar_endpoint(
    body: SimilarRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> SimilarResponse:
    """Embed the question, return the closest entities by cosine distance.

    Useful for open-ended queries the structured-filter mode can't
    capture ("companies similar to acme.example", "leads that look like
    artisanal bakeries"). Only entities that have already been embedded
    are searchable — run scripts/embed_entities.py to backfill.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENAI_API_KEY is not configured on the server.",
        )

    client = OpenAIEmbeddings(api_key=settings.openai_api_key)
    vectors = await client.embed([body.question])
    if not vectors:
        return SimilarResponse(question=body.question, match_count=0, rows=[])
    query_vector = vectors[0]

    rows = await _similarity_search(
        db, current_user.tenant_id, query_vector, body.limit
    )
    return SimilarResponse(
        question=body.question,
        match_count=len(rows),
        rows=rows,
    )


async def _similarity_search(
    db: AsyncSession, tenant_id: UUID, query_vector: list[float], limit: int
) -> list[SimilarRow]:
    """Run the cosine-distance lookup via textual SQL.

    Uses raw SQL on purpose — `entities.embedding` is added by migration
    a6d2f8b1e094 but isn't mapped on the Entity ORM class (see the
    comment in app/db/models.py). The pgvector `<=>` operator is cosine
    distance; lower is closer.
    """
    # pgvector accepts a string '[0.1, 0.2, ...]' for a vector literal.
    # Sending it as a parameter avoids any float-formatting surprises.
    vector_literal = "[" + ",".join(repr(float(v)) for v in query_vector) + "]"

    stmt = text(
        """
        SELECT
            entities.id,
            entities.job_id,
            entities.name,
            entities.email,
            entities.website,
            entities.city,
            entities.country,
            entities.category,
            entities.quality_score,
            entities.lead_status,
            (entities.embedding <=> CAST(:qvec AS vector)) AS distance
        FROM entities
        JOIN jobs ON entities.job_id = jobs.id
        WHERE jobs.tenant_id = :tenant_id
          AND entities.embedding IS NOT NULL
          AND entities.duplicate_of IS NULL
          AND entities.review_status NOT IN ('rejected', 'duplicate')
        ORDER BY distance ASC
        LIMIT :limit
        """
    ).bindparams(
        bindparam("qvec"),
        bindparam("tenant_id"),
        bindparam("limit"),
    )

    rows = (
        await db.execute(
            stmt, {"qvec": vector_literal, "tenant_id": tenant_id, "limit": limit}
        )
    ).mappings().all()

    out: list[SimilarRow] = []
    for r in rows:
        # Cosine distance is in [0, 2]; map to a [0, 1] similarity score
        # without changing the ranking. UI is friendlier with "0.92" than
        # "0.16 distance".
        sim = max(0.0, 1.0 - float(r["distance"]) / 2.0)
        out.append(
            SimilarRow(
                id=r["id"],
                job_id=r["job_id"],
                name=r["name"],
                email=r["email"],
                website=r["website"],
                city=r["city"],
                country=r["country"],
                category=r["category"],
                quality_score=r["quality_score"],
                lead_status=r["lead_status"],
                similarity=sim,
            )
        )
    return out
