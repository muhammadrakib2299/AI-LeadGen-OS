"""Webhook management — list, create, update, delete, recent deliveries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import User, Webhook, WebhookDelivery
from app.db.session import get_session
from app.services.webhooks import generate_secret

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookCreateRequest(BaseModel):
    url: HttpUrl
    events: list[str] = Field(default_factory=lambda: ["job.completed"], min_length=1)


class WebhookUpdateRequest(BaseModel):
    enabled: bool | None = None
    events: list[str] | None = Field(default=None, min_length=1)


class WebhookResponse(BaseModel):
    id: UUID
    url: str
    events: list[str]
    enabled: bool
    last_delivery_at: datetime | None
    failures_count: int
    created_at: datetime


class WebhookCreateResponse(WebhookResponse):
    # Secret is returned exactly once, on create. Store it on your side.
    secret: str


class WebhookListResponse(BaseModel):
    items: list[WebhookResponse]
    total: int


class DeliveryResponse(BaseModel):
    id: UUID
    event_type: str
    response_status: int | None
    duration_ms: int | None
    error: str | None
    created_at: datetime


class DeliveryListResponse(BaseModel):
    items: list[DeliveryResponse]
    total: int


def _to_response(w: Webhook) -> WebhookResponse:
    return WebhookResponse(
        id=w.id,
        url=w.url,
        events=[e.strip() for e in (w.events or "").split(",") if e.strip()],
        enabled=w.enabled,
        last_delivery_at=w.last_delivery_at,
        failures_count=w.failures_count,
        created_at=w.created_at,
    )


@router.post("", response_model=WebhookCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> WebhookCreateResponse:
    secret = generate_secret()
    hook = Webhook(
        tenant_id=current_user.tenant_id,
        url=str(body.url),
        secret=secret,
        events=",".join(body.events),
    )
    db.add(hook)
    await db.commit()
    await db.refresh(hook)
    log.info("webhook_created", webhook_id=str(hook.id), tenant_id=str(hook.tenant_id))
    base = _to_response(hook)
    return WebhookCreateResponse(
        **base.model_dump(),
        secret=secret,
    )


@router.get("", response_model=WebhookListResponse)
async def list_webhooks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> WebhookListResponse:
    rows = (
        await db.execute(
            select(Webhook)
            .where(Webhook.tenant_id == current_user.tenant_id)
            .order_by(Webhook.created_at.desc())
        )
    ).scalars().all()
    return WebhookListResponse(items=[_to_response(r) for r in rows], total=len(rows))


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: UUID,
    body: WebhookUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    hook = await _get_hook_for_tenant(db, webhook_id, current_user.tenant_id)
    if body.enabled is not None:
        hook.enabled = body.enabled
    if body.events is not None:
        hook.events = ",".join(body.events)
    await db.commit()
    await db.refresh(hook)
    return _to_response(hook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    hook = await _get_hook_for_tenant(db, webhook_id, current_user.tenant_id)
    await db.delete(hook)
    await db.commit()


@router.get("/{webhook_id}/deliveries", response_model=DeliveryListResponse)
async def list_deliveries(
    webhook_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> DeliveryListResponse:
    # Authorize via the hook itself so tenants can't peek at others' history.
    await _get_hook_for_tenant(db, webhook_id, current_user.tenant_id)
    rows = (
        await db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    items = [
        DeliveryResponse(
            id=r.id,
            event_type=r.event_type,
            response_status=r.response_status,
            duration_ms=r.duration_ms,
            error=r.error,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return DeliveryListResponse(items=items, total=len(items))


async def _get_hook_for_tenant(
    db: AsyncSession, webhook_id: UUID, tenant_id: UUID
) -> Webhook:
    stmt = select(Webhook).where(
        Webhook.id == webhook_id, Webhook.tenant_id == tenant_id
    )
    hook = (await db.execute(stmt)).scalar_one_or_none()
    if hook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="webhook not found"
        )
    return hook
