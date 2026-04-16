"""FastAPI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health, privacy
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

app.include_router(health.router)
app.include_router(privacy.router)
