"""Tests for PlacesClient. HTTP mocked with respx."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.services.places import PLACES_BASE_URL, PlacesClient

SAMPLE_RESPONSE: dict[str, Any] = {
    "places": [
        {
            "id": "ChIJ_test_1",
            "displayName": {"text": "Le Petit Bistro", "languageCode": "fr"},
            "formattedAddress": "12 Rue de Rivoli, 75004 Paris, France",
            "addressComponents": [
                {"longText": "Paris", "shortText": "Paris", "types": ["locality"]},
                {"longText": "France", "shortText": "FR", "types": ["country"]},
            ],
            "location": {"latitude": 48.8566, "longitude": 2.3522},
            "types": ["restaurant", "food"],
            "primaryType": "restaurant",
            "websiteUri": "https://lepetitbistro.example.fr",
            "nationalPhoneNumber": "01 42 00 00 00",
            "internationalPhoneNumber": "+33 1 42 00 00 00",
            "rating": 4.5,
            "userRatingCount": 212,
        }
    ]
}


@pytest.fixture
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


@respx.mock
async def test_text_search_parses_response(http_client: httpx.AsyncClient) -> None:
    route = respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
    )

    client = PlacesClient(http=http_client, api_key="test-key")
    places = await client.text_search("restaurants in Paris", region_code="FR")

    assert route.called
    assert len(places) == 1
    place = places[0]
    assert place.id == "ChIJ_test_1"
    assert place.name == "Le Petit Bistro"
    assert place.website_uri == "https://lepetitbistro.example.fr"
    assert place.country_code() == "FR"
    assert place.city() == "Paris"


@respx.mock
async def test_text_search_sends_correct_headers_and_body(
    http_client: httpx.AsyncClient,
) -> None:
    route = respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json={"places": []})
    )

    client = PlacesClient(http=http_client, api_key="sekret-key")
    await client.text_search("cafes in Lisbon", region_code="PT", language_code="pt", max_results=5)

    request = route.calls.last.request
    assert request.headers["X-Goog-Api-Key"] == "sekret-key"
    assert "places.displayName" in request.headers["X-Goog-FieldMask"]
    body = request.content.decode()
    assert "cafes in Lisbon" in body
    assert '"regionCode":"PT"' in body
    assert '"maxResultCount":5' in body


@respx.mock
async def test_text_search_raises_on_http_error(http_client: httpx.AsyncClient) -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(403, json={"error": {"message": "bad key"}})
    )

    client = PlacesClient(http=http_client, api_key="test-key")
    with pytest.raises(httpx.HTTPStatusError):
        await client.text_search("restaurants in Paris")


async def test_missing_api_key_raises_before_network() -> None:
    client = PlacesClient(http=httpx.AsyncClient(), api_key="")
    with pytest.raises(RuntimeError, match="GOOGLE_PLACES_API_KEY"):
        await client.text_search("restaurants in Paris")


@respx.mock
async def test_empty_places_list(http_client: httpx.AsyncClient) -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(200, json={})
    )
    client = PlacesClient(http=http_client, api_key="test-key")
    assert await client.text_search("nowhere") == []
