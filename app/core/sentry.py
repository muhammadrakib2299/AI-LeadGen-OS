"""Sentry error tracking. PII scrubbing enforced per compliance.md §9."""

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.core.config import get_settings


def configure_sentry() -> None:
    settings = get_settings()
    if not settings.sentry_dsn:
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1 if settings.environment == "prod" else 1.0,
        send_default_pii=False,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
            AsyncioIntegration(),
        ],
        before_send=_scrub_pii,
    )


def _scrub_pii(event: dict, _hint: dict) -> dict:
    for key in ("email", "phone", "address"):
        if event.get("extra", {}).get(key):
            event["extra"][key] = "[redacted]"
    return event
