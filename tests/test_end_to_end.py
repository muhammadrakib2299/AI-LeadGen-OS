"""End-to-end Phase 1 smoke test.

Exercises: POST /jobs -> background pipeline -> GET /jobs/{id} -> CSV export.
All external HTTP is mocked via respx; the LLM is a FakeLLM. A real Postgres
instance is used via the db_session fixture.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.db.session import get_session
from app.extractors.contacts import ContactsExtractor
from app.main import app
from app.services.crawler import Crawler
from app.services.job_runner import JobRunner
from app.services.places import PLACES_BASE_URL, PlacesClient
from app.services.query_validator import QueryValidator

SITE_BASE = "https://lepetitbistro.example.fr"


class _FakeLLM:
    async def complete_json(
        self, system: str, user: str, *, model: str = "", max_tokens: int = 0
    ) -> dict[str, Any]:
        return {
            "entity_type": "restaurant",
            "city": "Paris",
            "country": "FR",
            "keywords": [],
            "confidence": 0.95,
            "reason_if_low_confidence": "",
        }


_PLACES_PAYLOAD = {
    "places": [
        {
            "id": "ChIJ_e2e_1",
            "displayName": {"text": "Le Petit Bistro", "languageCode": "fr"},
            "formattedAddress": "12 Rue de Rivoli, 75004 Paris, France",
            "addressComponents": [
                {"longText": "Paris", "shortText": "Paris", "types": ["locality"]},
                {"longText": "France", "shortText": "FR", "types": ["country"]},
            ],
            "location": {"latitude": 48.8566, "longitude": 2.3522},
            "types": ["restaurant"],
            "primaryType": "restaurant",
            "websiteUri": SITE_BASE,
            "nationalPhoneNumber": "01 42 00 00 00",
        }
    ]
}

_SITE_HTML = (
    "<html><body>"
    '<a href="mailto:contact@lepetitbistro.example.fr">Email us</a>'
    '<a href="tel:+33142000000">Call</a>'
    '<a href="https://www.linkedin.com/company/le-petit-bistro">LinkedIn</a>'
    "</body></html>"
)


def _install_http_mocks() -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=_PLACES_PAYLOAD)
    )
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(
        return_value=httpx.Response(
            200, text=_SITE_HTML, headers={"content-type": "text/html; charset=utf-8"}
        )
    )
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(
        return_value=httpx.Response(404, text="", headers={"content-type": "text/html"})
    )


def _test_background_factory(session: AsyncSession):
    """Return a coroutine that runs the pipeline using the test session + fakes."""

    async def _bg(job_id: UUID) -> None:
        job = await session.get(Job, job_id)
        if job is None:
            return
        async with httpx.AsyncClient(follow_redirects=True) as http:
            runner = JobRunner(
                validator=QueryValidator(_FakeLLM()),
                places=PlacesClient(http=http, api_key="test-key", session=session),
                crawler=Crawler(http=http, session=session, per_domain_interval_s=0),
                extractor=ContactsExtractor(llm=None),
                session=session,
            )
            await runner.run(job)

    return _bg


@pytest.mark.asyncio
@respx.mock
async def test_post_job_then_poll_then_export_csv(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_http_mocks()

    from app.api import jobs as jobs_api

    monkeypatch.setattr(jobs_api, "_run_in_background", _test_background_factory(db_session))

    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Submit
            post = await client.post(
                "/jobs",
                json={"query": "restaurants in Paris", "limit": 5, "budget_cap_usd": 5.0},
            )
            assert post.status_code == 201
            job_id = post.json()["id"]
            # In tests the background is monkey-patched to run inline, so the
            # POST response status may already be terminal. Production paths
            # enqueue to arq and return "pending" immediately.

            # 2. Wait for background task to complete.
            body: dict[str, Any] = {}
            for _ in range(200):
                await asyncio.sleep(0.05)
                poll = await client.get(f"/jobs/{job_id}")
                body = poll.json()
                if body["status"] not in {"pending", "running"}:
                    break

            assert body["status"] == "succeeded", body
            assert body["entity_count"] == 1
            assert body["cost_usd"] > 0
            assert body["query_validated"]["entity_type"] == "restaurant"
            assert body["query_validated"]["country"] == "FR"

            # 3. Export CSV
            export = await client.get(f"/jobs/{job_id}/export.csv")
            assert export.status_code == 200
            assert export.headers["content-type"].startswith("text/csv")
            csv_text = export.text
            header, *rows = csv_text.strip().splitlines()
            assert "name,website,email" in header
            assert len(rows) == 1
            row = rows[0]
            assert "Le Petit Bistro" in row
            assert "contact@lepetitbistro.example.fr" in row
            assert "+33142000000" in row
            assert "ChIJ_e2e_1" in row
            assert "crawler" in row  # provenance
    finally:
        app.dependency_overrides.clear()
