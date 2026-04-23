"""POST /ask — natural-language questions over the tenant's leads.

The LLM only sees the question and the schema. SQL execution stays in
this process; rows never round-trip through the model.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Entity, User
from app.db.session import get_session
from app.services.ask import AskFilter, AskSpec, ask
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
