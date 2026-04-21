"""FastAPI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    api_keys,
    auth,
    billing,
    blacklist,
    health,
    integrations,
    jobs,
    privacy,
    reverify,
    review,
    status,
    templates,
    webhooks,
)
from app.api import (
    settings as settings_api,
)
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.sentry import configure_sentry
from app.core.telemetry import configure_telemetry
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    configure_sentry()
    # Instrumentation lives behind an env flag; when unset, this is a no-op.
    configure_telemetry(_app, engine=get_engine())
    log = get_logger(__name__)
    settings = get_settings()
    log.info(
        "app_started",
        version=__version__,
        environment=settings.environment,
        jurisdiction=settings.jurisdiction,
    )
    yield
    log.info("app_stopped")


app = FastAPI(
    title="AI LeadGen OS",
    description="Compliant lead generation platform (EU/UK).",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # Credentials on so the browser will send the session cookie.
    allow_credentials=True,
)

# Public routes: health (monitoring), privacy opt-out (data subjects have no
# account), auth (login/register).
app.include_router(health.router)
app.include_router(privacy.router)
app.include_router(auth.router)

# Operator-only routes: gated at the router level.
_auth = [Depends(get_current_user)]
app.include_router(jobs.router, dependencies=_auth)
app.include_router(review.router, dependencies=_auth)
app.include_router(templates.router, dependencies=_auth)
app.include_router(blacklist.router, dependencies=_auth)
app.include_router(reverify.router, dependencies=_auth)
app.include_router(status.router, dependencies=_auth)
app.include_router(settings_api.router, dependencies=_auth)
app.include_router(webhooks.router, dependencies=_auth)
app.include_router(integrations.router, dependencies=_auth)
# api_keys and billing use get_current_user inside each handler; billing's
# /webhook is public and authenticates Stripe via signature verification.
app.include_router(api_keys.router)
app.include_router(billing.router)
