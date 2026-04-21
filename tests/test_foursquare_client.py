"""Tests for FoursquareClient + FoursquareAdapter. HTTP mocked with respx."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.services.discovery import FoursquareAdapter
from app.services.foursquare import (
    FOURSQUARE_BASE_URL,
    FoursquareClient,
    _parse_places,
)

SEARCH_URL = f"{FOURSQUARE_BASE_URL}/places/search"

SAMPLE_PAYLOAD: dict[str, Any] = {
    "results": [
        {
            "fsq_id": "12345",
            "name": "Café de la Paix",
            "tel": "+33140123456",
            "website": "https://cafedelapaix.example",
            "location": {
                "address": "5 Place de l'Opéra",
                "locality": "Paris",
                "postcode": "75009",
                "country": "FR",
                "formatted_address": "5 Place de l'Opéra, 75009 Paris, France",
            },
            "geocodes": {"main": {"latitude": 48.8711, "longitude": 2.3317}},
            "categories": [{"id": 123, "name": "Café"}],
        },
        {"name": "missing fsq_id"},  # skipped
    ]
}


@pytest.fixture
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


def test_parse_places_drops_rows_without_id() -> None:
    rows = _parse_places(SAMPLE_PAYLOAD)
    assert len(rows) == 1
    assert rows[0].fsq_id == "12345"
    assert rows[0].location and rows[0].location.country == "FR"


async def test_search_requires_location(http_client: httpx.AsyncClient) -> None:
    client = FoursquareClient(http=http_client, api_key="fake")
    with pytest.raises(ValueError):
        await client.search_places("café")


@respx.mock
async def test_auth_header_is_raw_key_no_bearer_prefix(
    http_client: httpx.AsyncClient,
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = FoursquareClient(http=http_client, api_key="fsq-secret")
    await client.search_places("café", near="Paris")

    req = route.calls.last.request
    # v3 uses the raw key, unlike Yelp's Bearer scheme.
    assert req.headers["Authorization"] == "fsq-secret"
    assert "fsq-secret" not in str(req.url)


@respx.mock
async def test_adapter_maps_to_place(http_client: httpx.AsyncClient) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SAMPLE_PAYLOAD))
    adapter = FoursquareAdapter(FoursquareClient(http=http_client, api_key="fake"))

    places = await adapter.search(
        "café in Paris", region_code="FR", max_results=10, job_id=None
    )
    assert len(places) == 1
    p = places[0]
    assert p.id == "fsq:12345"
    assert p.name == "Café de la Paix"
    assert p.website_uri == "https://cafedelapaix.example"
    assert p.national_phone_number == "+33140123456"
    assert p.country_code() == "FR"
    assert p.city() == "Paris"


@respx.mock
async def test_adapter_skips_without_location(http_client: httpx.AsyncClient) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    adapter = FoursquareAdapter(FoursquareClient(http=http_client, api_key="fake"))
    places = await adapter.search("café", region_code=None, max_results=10, job_id=None)
    assert places == []
    assert not route.called


def test_adapter_is_tier1_official_api() -> None:
    adapter = FoursquareAdapter(FoursquareClient(api_key="fake"))
    # Compliant Mode keeps Foursquare; Yelp gets dropped.
    assert adapter.is_official_api is True
