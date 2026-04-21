"""Tests for YelpClient. HTTP mocked with respx."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RawFetch
from app.services.discovery import YelpAdapter, _term_from_query
from app.services.yelp import (
    SOURCE_SLUG,
    YELP_BASE_URL,
    YelpClient,
    _parse_businesses,
)

SEARCH_URL = f"{YELP_BASE_URL}/businesses/search"

SAMPLE_PAYLOAD: dict[str, Any] = {
    "businesses": [
        {
            "id": "pizza-mario-paris",
            "alias": "pizza-mario-paris",
            "name": "Pizza Mario",
            "phone": "+33140123456",
            "display_phone": "+33 1 40 12 34 56",
            "is_closed": False,
            "location": {
                "address1": "12 rue de la Paix",
                "city": "Paris",
                "zip_code": "75002",
                "country": "FR",
                "state": "75",
                "display_address": ["12 rue de la Paix", "75002 Paris", "France"],
            },
            "coordinates": {"latitude": 48.8685, "longitude": 2.3318},
        },
        # Row missing required `id` — must be skipped.
        {"name": "Ghost"},
    ],
    "total": 1,
}


@pytest.fixture
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


def test_parse_businesses_drops_rows_without_id() -> None:
    rows = _parse_businesses(SAMPLE_PAYLOAD)
    assert len(rows) == 1
    assert rows[0].id == "pizza-mario-paris"
    assert rows[0].formatted_address == "12 rue de la Paix, 75002 Paris, France"


async def test_search_businesses_requires_location(http_client: httpx.AsyncClient) -> None:
    client = YelpClient(http=http_client, api_key="fake")
    with pytest.raises(ValueError):
        await client.search_businesses("pizza")


@respx.mock
async def test_search_businesses_sends_bearer_header(
    http_client: httpx.AsyncClient,
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"businesses": []})
    )

    client = YelpClient(http=http_client, api_key="yelp-secret")
    await client.search_businesses("pizza", location="Paris")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer yelp-secret"
    # Secret must never land on the query string.
    assert "yelp-secret" not in str(request.url)


@respx.mock
async def test_search_businesses_records_audit(
    http_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SAMPLE_PAYLOAD))

    client = YelpClient(http=http_client, api_key="fake", session=db_session)
    hits = await client.search_businesses("pizza", location="Paris")

    assert len(hits) == 1
    row = (
        await db_session.execute(
            select(RawFetch).where(RawFetch.source_slug == SOURCE_SLUG)
        )
    ).scalar_one()
    assert row.response_status == 200
    # Audit URL must never carry the bearer.
    assert "yelp-secret" not in row.url
    assert "term=pizza" in row.url


@respx.mock
async def test_search_businesses_caches_within_24h(
    http_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE_PAYLOAD)
    )

    client = YelpClient(http=http_client, api_key="fake", session=db_session)
    first = await client.search_businesses("pizza", location="Paris")
    second = await client.search_businesses("pizza", location="Paris")

    assert route.call_count == 1, "second identical call should hit the cache"
    assert [b.id for b in first] == [b.id for b in second]


@respx.mock
async def test_search_businesses_ignores_cache_with_null_payload(
    http_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """A row where the retention sweep already nulled payload must NOT be reused."""
    # Pre-seed a raw_fetch mimicking what the sweep leaves behind.
    db_session.add(
        RawFetch(
            source_slug=SOURCE_SLUG,
            url=f"{SEARCH_URL}?term=pizza&limit=20&location=Paris",
            method="GET",
            legal_basis="legitimate_interest",
            response_status=200,
            content_hash="will-not-match",
            payload=None,  # nulled by retention sweep
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    await db_session.flush()

    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE_PAYLOAD)
    )

    client = YelpClient(http=http_client, api_key="fake", session=db_session)
    hits = await client.search_businesses("pizza", location="Paris")

    assert route.called
    assert len(hits) == 1


@respx.mock
async def test_search_businesses_records_audit_on_http_error(
    http_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )

    client = YelpClient(http=http_client, api_key="fake", session=db_session)
    with pytest.raises(httpx.HTTPStatusError):
        await client.search_businesses("pizza", location="Paris")

    row = (
        await db_session.execute(
            select(RawFetch).where(RawFetch.source_slug == SOURCE_SLUG)
        )
    ).scalar_one()
    assert row.response_status == 429


def test_term_from_query_strips_trailing_location() -> None:
    assert _term_from_query("restaurants in Paris") == "restaurants"
    assert _term_from_query("coffee shops in Berlin") == "coffee shops"
    # No "in" → returned as-is (stripped).
    assert _term_from_query("bakeries") == "bakeries"


@respx.mock
async def test_yelp_adapter_maps_business_to_place(
    http_client: httpx.AsyncClient,
) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SAMPLE_PAYLOAD))
    client = YelpClient(http=http_client, api_key="fake")
    adapter = YelpAdapter(client)

    places = await adapter.search(
        "pizza in Paris",
        region_code="FR",
        max_results=10,
        job_id=None,
    )
    assert len(places) == 1
    p = places[0]
    assert p.id == "yelp:pizza-mario-paris"
    assert p.name == "Pizza Mario"
    assert p.country_code() == "FR"
    assert p.city() == "Paris"
    # Yelp curated data (rating, price, categories) not carried over.
    assert p.rating is None


@respx.mock
async def test_yelp_adapter_skips_when_no_location(
    http_client: httpx.AsyncClient,
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"businesses": []})
    )
    client = YelpClient(http=http_client, api_key="fake")
    adapter = YelpAdapter(client)

    # No "in <city>" and no region_code → adapter must bail without hitting Yelp.
    places = await adapter.search("pizza", region_code=None, max_results=10, job_id=None)
    assert places == []
    assert not route.called
