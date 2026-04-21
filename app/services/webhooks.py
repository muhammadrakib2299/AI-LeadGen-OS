"""Outbound webhook delivery.

Signs each payload with HMAC-SHA256 over the exact request body using the
webhook's `secret`. Recipients recompute the HMAC and compare via
`X-LeadGen-Signature` (hex). `X-LeadGen-Event` carries the event type.

Best-effort delivery: a failure increments `failures_count` and is logged
to `webhook_deliveries`. No automatic retries in v1 — operators can
re-enable a failing hook from the UI once they've fixed the receiver.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets as _secrets
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Webhook, WebhookDelivery

log = get_logger(__name__)

DELIVERY_TIMEOUT_S = 10.0


def generate_secret() -> str:
    return _secrets.token_hex(32)


def sign_payload(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def fan_out_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    http: httpx.AsyncClient | None = None,
) -> int:
    """Deliver `event_type` to every enabled webhook matching it for this tenant.

    Returns the number of webhooks attempted. Never raises — a bad
    receiver should not fail the calling job.
    """
    hooks = (
        await session.execute(
            select(Webhook).where(
                Webhook.tenant_id == tenant_id,
                Webhook.enabled.is_(True),
            )
        )
    ).scalars().all()
    selected = [h for h in hooks if event_type in _split_events(h.events)]
    if not selected:
        return 0

    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")

    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_S)
    try:
        for hook in selected:
            await _deliver_one(session, hook, event_type, body, client)
    finally:
        if owns_http:
            await client.aclose()
    return len(selected)


async def _deliver_one(
    session: AsyncSession,
    hook: Webhook,
    event_type: str,
    body: bytes,
    client: httpx.AsyncClient,
) -> None:
    signature = sign_payload(hook.secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-LeadGen-Event": event_type,
        "X-LeadGen-Signature": signature,
        "X-LeadGen-Webhook-Id": str(hook.id),
    }
    started = time.monotonic()
    status_code: int | None = None
    error: str | None = None
    try:
        response = await client.post(hook.url, content=body, headers=headers)
        status_code = response.status_code
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"
    duration_ms = int((time.monotonic() - started) * 1000)

    hook.last_delivery_at = datetime.now(UTC)
    ok = status_code is not None and 200 <= status_code < 300
    if ok:
        hook.failures_count = 0
    else:
        hook.failures_count = (hook.failures_count or 0) + 1

    payload_preview = _decode_payload(body)
    session.add(
        WebhookDelivery(
            webhook_id=hook.id,
            event_type=event_type,
            response_status=status_code,
            duration_ms=duration_ms,
            error=error,
            payload=payload_preview,
        )
    )
    await session.flush()
    log.info(
        "webhook_delivered",
        webhook_id=str(hook.id),
        event_type=event_type,
        status=status_code,
        ok=ok,
    )


def _split_events(events: str) -> list[str]:
    return [e.strip() for e in (events or "").split(",") if e.strip()]


def _decode_payload(body: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
