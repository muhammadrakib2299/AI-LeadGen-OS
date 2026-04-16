"""Integration tests — hit the real Postgres instance via docker-compose.

These tests verify audit-log + cache behavior that pure HTTP mocks cannot cover.
They are skipped automatically if Postgres is unreachable (see conftest).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RawFetch
from app.services.places import PLACES_BASE_URL, SOURCE_SLUG, PlacesClient

SAMPLE = {
    "places": [
        {
            "id": "ChIJ_int_1",
            "displayName": {"text": "Café Central"},
            "formattedAddress": "Herrengasse 14, 1010 Vienna, Austria",
            "addressComponents": [
                {"longText": "Vienna", "shortText": "Vienna", "types": ["locality"]},
                {"longText": "Austria", "shortText": "AT", "types": ["country"]},
            ],
            "websiteUri": "https://cafecentral.example.at",
        }
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_places_fetch_writes_audit_row(db_session: AsyncSession) -> None:
    route = respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=SAMPLE)
    )

    async with httpx.AsyncClient() as http:
        client = PlacesClient(http=http, api_key="test-key", session=db_session)
        places = await client.text_search("cafes in Vienna", region_code="AT")

    assert route.call_count == 1
    assert len(places) == 1

    rows = (
        (await db_session.execute(select(RawFetch).where(RawFetch.source_slug == SOURCE_SLUG)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.response_status == 200
    assert row.method == "POST"
    assert row.legal_basis == "legitimate_interest"
    assert row.payload is not None
    assert row.payload["places"][0]["id"] == "ChIJ_int_1"
    assert row.bytes_fetched and row.bytes_fetched > 0
    assert row.cost_usd > 0


@pytest.mark.asyncio
@respx.mock
async def test_places_cache_hit_skips_network(db_session: AsyncSession) -> None:
    route = respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=SAMPLE)
    )

    async with httpx.AsyncClient() as http:
        client = PlacesClient(http=http, api_key="test-key", session=db_session)
        await client.text_search("cafes in Vienna", region_code="AT")
        await client.text_search("cafes in Vienna", region_code="AT")  # same args

    # Second call must be served from cache — exactly one network call total.
    assert route.call_count == 1

    rows = (
        (await db_session.execute(select(RawFetch).where(RawFetch.source_slug == SOURCE_SLUG)))
        .scalars()
        .all()
    )
    assert len(rows) == 1  # no second audit row because cache short-circuited
