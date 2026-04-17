"""End-to-end JobRunner tests against the real Postgres instance.

HTTP is fully mocked with respx; no real Google / Anthropic calls.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.db.models import Blacklist, Entity, Job
from app.extractors.contacts import ContactsExtractor
from app.services.crawler import Crawler
from app.services.job_runner import JobRunner
from app.services.places import PLACES_BASE_URL, PlacesClient
from app.services.query_validator import QueryValidator

SITE_HOST = "lepetitbistro.example.fr"
SITE_BASE = f"https://{SITE_HOST}"


def _places_response(with_website: bool = True) -> dict[str, Any]:
    place: dict[str, Any] = {
        "id": "ChIJ_bistro_1",
        "displayName": {"text": "Le Petit Bistro", "languageCode": "fr"},
        "formattedAddress": "12 Rue de Rivoli, 75004 Paris, France",
        "addressComponents": [
            {"longText": "Paris", "shortText": "Paris", "types": ["locality"]},
            {"longText": "France", "shortText": "FR", "types": ["country"]},
        ],
        "location": {"latitude": 48.8566, "longitude": 2.3522},
        "types": ["restaurant"],
        "primaryType": "restaurant",
        "nationalPhoneNumber": "01 42 00 00 00",
    }
    if with_website:
        place["websiteUri"] = SITE_BASE
    return {"places": [place]}


class FakeLLM:
    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = "",
        max_tokens: int = 0,
        tier: str = "fast",
    ) -> dict[str, Any]:
        return {"entity_type": "restaurant", "city": "Paris", "country": "FR", "confidence": 0.95}


def _text_html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html"})


async def _make_runner(db_session, *, llm_contacts: bool = False) -> JobRunner:
    http = httpx.AsyncClient()
    places = PlacesClient(http=http, api_key="test-key", session=db_session)
    crawler = Crawler(http=http, session=db_session, per_domain_interval_s=0)
    extractor = ContactsExtractor(llm=None)  # regex-only; sufficient for tests
    validator = QueryValidator(FakeLLM())
    return JobRunner(
        validator=validator,
        places=places,
        crawler=crawler,
        extractor=extractor,
        session=db_session,
    )


@pytest.mark.asyncio
@respx.mock
async def test_job_runner_happy_path_discovers_crawls_extracts_persists(db_session) -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=_places_response())
    )
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(
        return_value=_text_html(
            "<html><body>"
            '<a href="mailto:contact@lepetitbistro.example.fr">email</a>'
            '<a href="tel:+33142000000">phone</a>'
            '<a href="https://www.linkedin.com/company/le-petit-bistro">li</a>'
            "</body></html>"
        )
    )
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(
        return_value=httpx.Response(404, text="", headers={"content-type": "text/html"})
    )

    job = Job(query_raw="restaurants in Paris", limit=5, budget_cap_usd=5.0)
    db_session.add(job)
    await db_session.flush()

    runner = await _make_runner(db_session)
    await runner.run(job)

    assert job.status == "succeeded"
    assert job.cost_usd and float(job.cost_usd) > 0
    assert job.query_validated and job.query_validated["entity_type"] == "restaurant"

    entities = (
        (await db_session.execute(select(Entity).where(Entity.job_id == job.id))).scalars().all()
    )
    assert len(entities) == 1
    ent = entities[0]
    assert ent.name == "Le Petit Bistro"
    assert ent.email == "contact@lepetitbistro.example.fr"
    assert ent.phone == "+33142000000"
    assert ent.country == "FR"
    assert ent.city == "Paris"
    assert ent.domain == SITE_HOST
    assert ent.external_ids["google_place_id"] == "ChIJ_bistro_1"
    assert ent.field_sources["email"]["source"] == "crawler"
    assert ent.field_sources["website"]["source"] == "google_places"
    assert ent.socials and "linkedin" in ent.socials
    # All fields present + high source trust + fresh → high score, approved.
    assert ent.quality_score is not None and ent.quality_score >= 90
    assert ent.review_status == "approved"


@pytest.mark.asyncio
@respx.mock
async def test_job_runner_rejects_invalid_query(db_session) -> None:
    # No HTTP calls needed — validator will reject before hitting Places.
    class RejectingLLM:
        async def complete_json(self, system: str, user: str, **kw: Any) -> dict[str, Any]:
            return {
                "entity_type": "business",
                "confidence": 0.1,
                "reason_if_low_confidence": "too vague",
            }

    job = Job(query_raw="give me some companies", limit=10, budget_cap_usd=5.0)
    db_session.add(job)
    await db_session.flush()

    http = httpx.AsyncClient()
    runner = JobRunner(
        validator=QueryValidator(RejectingLLM()),
        places=PlacesClient(http=http, api_key="test-key", session=db_session),
        crawler=Crawler(http=http, session=db_session, per_domain_interval_s=0),
        extractor=ContactsExtractor(llm=None),
        session=db_session,
    )
    await runner.run(job)

    assert job.status == "rejected"
    assert job.error
    entities = (
        (await db_session.execute(select(Entity).where(Entity.job_id == job.id))).scalars().all()
    )
    assert entities == []


@pytest.mark.asyncio
@respx.mock
async def test_job_runner_budget_guard_stops_pipeline(db_session) -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=_places_response())
    )
    # Cap is far below the Places call cost.
    job = Job(query_raw="restaurants in Paris", limit=5, budget_cap_usd=0.0001)
    db_session.add(job)
    await db_session.flush()

    runner = await _make_runner(db_session)
    await runner.run(job)

    assert job.status == "budget_exceeded"
    entities = (
        (await db_session.execute(select(Entity).where(Entity.job_id == job.id))).scalars().all()
    )
    assert entities == []


@pytest.mark.asyncio
@respx.mock
async def test_job_runner_skips_blacklisted_domain(db_session) -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=_places_response())
    )
    db_session.add(Blacklist(domain=SITE_HOST, reason="opt-out request"))
    await db_session.flush()

    job = Job(query_raw="restaurants in Paris", limit=5, budget_cap_usd=5.0)
    db_session.add(job)
    await db_session.flush()

    runner = await _make_runner(db_session)
    await runner.run(job)

    assert job.status == "succeeded"
    entities = (
        (await db_session.execute(select(Entity).where(Entity.job_id == job.id))).scalars().all()
    )
    assert entities == []


@pytest.mark.asyncio
@respx.mock
async def test_job_runner_handles_place_with_no_website(db_session) -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=_places_response(with_website=False))
    )

    job = Job(query_raw="restaurants in Paris", limit=5, budget_cap_usd=5.0)
    db_session.add(job)
    await db_session.flush()

    runner = await _make_runner(db_session)
    await runner.run(job)

    assert job.status == "succeeded"
    entities = (
        (await db_session.execute(select(Entity).where(Entity.job_id == job.id))).scalars().all()
    )
    # Still persisted — Places gave us name/address/phone, just no crawl data.
    assert len(entities) == 1
    ent = entities[0]
    assert ent.website is None
    assert ent.domain is None
    # Places national number "01 42 00 00 00" with FR region → E.164.
    assert ent.phone == "+33142000000"
