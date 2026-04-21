"""Saved search templates — one-click re-run for common queries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import SearchTemplate, User
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=3, max_length=500)
    default_limit: int = Field(default=100, ge=1, le=1000)
    default_budget_cap_usd: float = Field(default=5.0, gt=0, le=100.0)


class TemplateResponse(BaseModel):
    id: UUID
    name: str
    query: str
    default_limit: int
    default_budget_cap_usd: float
    created_at: datetime


class TemplateListResponse(BaseModel):
    items: list[TemplateResponse]
    total: int


def _to_response(t: SearchTemplate) -> TemplateResponse:
    return TemplateResponse(
        id=t.id,
        name=t.name,
        query=t.query,
        default_limit=t.default_limit,
        default_budget_cap_usd=float(t.default_budget_cap_usd),
        created_at=t.created_at,
    )


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    payload: TemplateCreateRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TemplateResponse:
    template = SearchTemplate(
        tenant_id=current_user.tenant_id,
        name=payload.name,
        query=payload.query,
        default_limit=payload.default_limit,
        default_budget_cap_usd=payload.default_budget_cap_usd,
    )
    session.add(template)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"template name '{payload.name}' already exists"
        ) from None
    await session.refresh(template)
    log.info("template_created", template_id=str(template.id), name=template.name)
    return _to_response(template)


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TemplateListResponse:
    stmt = (
        select(SearchTemplate)
        .where(SearchTemplate.tenant_id == current_user.tenant_id)
        .order_by(SearchTemplate.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return TemplateListResponse(items=[_to_response(t) for t in rows], total=len(rows))


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    # Fetch with tenant-scope so foreign-tenant templates return 404, not 403.
    # Surfacing existence of a row would leak cross-tenant info.
    stmt = select(SearchTemplate).where(
        SearchTemplate.id == template_id,
        SearchTemplate.tenant_id == current_user.tenant_id,
    )
    template = (await session.execute(stmt)).scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    await session.delete(template)
    await session.commit()
    log.info("template_deleted", template_id=str(template_id))
    return Response(status_code=204)
