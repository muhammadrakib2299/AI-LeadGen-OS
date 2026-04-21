"""Builds a production JobRunner with real API clients.

Tests build JobRunners directly with fakes; this factory exists for the
HTTP layer so each request can assemble a fresh pipeline with proper lifecycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.extractors.contacts import ContactsExtractor
from app.services.crawler import Crawler
from app.services.discovery import PlacesAdapter, SmartRouter, YelpAdapter
from app.services.job_runner import JobRunner
from app.services.llm import AnthropicClient, LLMClient
from app.services.places import PlacesClient
from app.services.query_validator import QueryValidator
from app.services.yelp import YelpClient


async def make_production_runner(
    session: AsyncSession,
) -> tuple[JobRunner, Callable[[], Awaitable[None]]]:
    """Return (runner, cleanup). Caller MUST `await cleanup()` after `runner.run`."""
    settings = get_settings()

    http = httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    places = PlacesClient(http=http, api_key=settings.google_places_api_key, session=session)
    crawler = Crawler(http=http, session=session)

    # Compose discovery adapters: Places is primary; Yelp is an optional
    # fallback, enabled only when a key is configured AND compliant mode is
    # off (Yelp's 24h storage rule makes it non-compliant for strict EU).
    adapters = [PlacesAdapter(places)]
    yelp: YelpClient | None = None
    if settings.yelp_api_key and not settings.compliant_mode:
        yelp = YelpClient(http=http, api_key=settings.yelp_api_key, session=session)
        adapters.append(YelpAdapter(yelp))
    router = SmartRouter(adapters, compliant_mode=settings.compliant_mode)

    llm: LLMClient | None = None
    if settings.anthropic_api_key:
        llm = AnthropicClient(api_key=settings.anthropic_api_key)

    if llm is None:
        raise RuntimeError("ANTHROPIC_API_KEY must be set — the query validator requires an LLM.")

    validator = QueryValidator(llm)
    extractor = ContactsExtractor(llm=llm)

    runner = JobRunner(
        validator=validator,
        router=router,
        crawler=crawler,
        extractor=extractor,
        session=session,
    )

    async def cleanup() -> None:
        await http.aclose()

    return runner, cleanup
