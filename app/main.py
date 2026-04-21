"""FastAPI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    api_keys,
    auth,
    blacklist,
    health,
    jobs,
    privacy,
    reverify,
    review,
    templates,
)
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.sentry import configure_sentry


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    configure_sentry()
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
# api_keys uses get_current_user internally so it can read the caller's id.
app.include_router(api_keys.router)
