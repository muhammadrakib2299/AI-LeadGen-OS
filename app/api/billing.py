"""Stripe billing endpoints.

Scope v1: upgrade from free → standard via Checkout. Downgrade and seat
changes come later.

Surface area:
- GET  /billing/status    — current tenant's plan + subscription id
- POST /billing/checkout  — returns a Stripe Checkout URL to redirect to
- POST /billing/webhook   — Stripe event receiver (public, signed)

The webhook is mounted outside the auth dependency list because Stripe
doesn't send session cookies or bearer tokens — signature verification is
the access control. Don't remove that verification.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Tenant, User
from app.db.session import get_session

log = get_logger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


class BillingStatusResponse(BaseModel):
    plan: str
    stripe_customer_id: str | None
    stripe_subscription_id: str | None


class CheckoutResponse(BaseModel):
    checkout_url: str


def _stripe_client() -> Any:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured on this deploy.",
        )
    stripe.api_key = settings.stripe_secret_key
    return stripe


async def _get_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:  # should never happen if the user session is valid
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


@router.get("/status", response_model=BillingStatusResponse)
async def billing_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> BillingStatusResponse:
    tenant = await _get_tenant(db, current_user.tenant_id)
    return BillingStatusResponse(
        plan=tenant.plan,
        stripe_customer_id=tenant.stripe_customer_id,
        stripe_subscription_id=tenant.stripe_subscription_id,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> CheckoutResponse:
    """Create (or reuse) a Stripe Customer, then a Checkout Session.

    The customer is persisted on the tenant so subsequent portal sessions
    and webhook matches are stable. The Checkout Session is single-use.
    """
    settings = get_settings()
    if not settings.stripe_price_id_standard:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Standard plan price is not configured.",
        )
    client = _stripe_client()
    tenant = await _get_tenant(db, current_user.tenant_id)

    if tenant.stripe_customer_id is None:
        customer = client.Customer.create(
            email=current_user.email,
            metadata={"tenant_id": str(tenant.id)},
        )
        tenant.stripe_customer_id = customer["id"]
        await db.commit()

    session = client.checkout.Session.create(
        mode="subscription",
        customer=tenant.stripe_customer_id,
        line_items=[{"price": settings.stripe_price_id_standard, "quantity": 1}],
        success_url=settings.stripe_success_url,
        cancel_url=settings.stripe_cancel_url,
        client_reference_id=str(tenant.id),
        metadata={"tenant_id": str(tenant.id)},
    )
    log.info("billing_checkout_created", tenant_id=str(tenant.id), session_id=session["id"])
    return CheckoutResponse(checkout_url=session["url"])


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> None:
    """Receive a Stripe event. Verifies the signature, then updates the tenant.

    We only care about three event types for v1:
      - checkout.session.completed    → plan = "standard", save subscription id
      - customer.subscription.updated → plan <- status (active→standard, past_due, canceled)
      - customer.subscription.deleted → plan = "canceled"
    Everything else is acknowledged but ignored.
    """
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook not configured.",
        )
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig,
            secret=settings.stripe_webhook_secret,
        )
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature",
        ) from exc

    await _apply_event(event, db)


async def _apply_event(event: dict[str, Any], db: AsyncSession) -> None:
    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        tenant_id = _tenant_id_from_metadata(obj)
        if tenant_id is None:
            return
        tenant = await _get_tenant(db, tenant_id)
        subscription_id = obj.get("subscription")
        tenant.plan = "standard"
        tenant.stripe_subscription_id = subscription_id
        await db.commit()
        log.info("billing_checkout_completed", tenant_id=str(tenant_id))
        return

    if event_type == "customer.subscription.updated":
        tenant = await _tenant_by_subscription(db, obj.get("id"))
        if tenant is None:
            return
        stripe_status = obj.get("status")
        plan = _plan_from_subscription_status(stripe_status)
        tenant.plan = plan
        await db.commit()
        log.info(
            "billing_subscription_updated",
            tenant_id=str(tenant.id),
            stripe_status=stripe_status,
            plan=plan,
        )
        return

    if event_type == "customer.subscription.deleted":
        tenant = await _tenant_by_subscription(db, obj.get("id"))
        if tenant is None:
            return
        tenant.plan = "canceled"
        tenant.stripe_subscription_id = None
        await db.commit()
        log.info("billing_subscription_deleted", tenant_id=str(tenant.id))
        return


def _tenant_id_from_metadata(obj: dict[str, Any]) -> UUID | None:
    # checkout.session carries client_reference_id; subscriptions/invoices
    # only have metadata. Check both shapes.
    ref = obj.get("client_reference_id")
    if ref:
        try:
            return UUID(ref)
        except ValueError:
            return None
    metadata = obj.get("metadata") or {}
    raw = metadata.get("tenant_id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


async def _tenant_by_subscription(
    db: AsyncSession, subscription_id: str | None
) -> Tenant | None:
    if not subscription_id:
        return None
    return (
        await db.execute(
            select(Tenant).where(Tenant.stripe_subscription_id == subscription_id)
        )
    ).scalar_one_or_none()


def _plan_from_subscription_status(stripe_status: str | None) -> str:
    # Stripe's subscription status vocabulary → our internal plan label.
    if stripe_status in ("active", "trialing"):
        return "standard"
    if stripe_status in ("past_due", "unpaid"):
        return "past_due"
    if stripe_status in ("canceled", "incomplete_expired"):
        return "canceled"
    return "free"
