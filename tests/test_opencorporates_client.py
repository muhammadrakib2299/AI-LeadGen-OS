"""Tests for OpenCorporatesClient. HTTP mocked with respx."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RawFetch
from app.services.opencorporates import (
    OPENCORPORATES_BASE_URL,
    SOURCE_SLUG,
    OpenCorporatesClient,
    _hash_request,
    _parse_companies,
)

SEARCH_URL = f"{OPENCORPORATES_BASE_URL}/companies/search"

SAMPLE_PAYLOAD: dict[str, Any] = {
    "results": {
        "companies": [
            {
                "company": {
                    "name": "ACME Holdings Ltd",
                    "company_number": "12345678",
                    "jurisdiction_code": "gb",
                    "registered_address_in_full": "1 Acme Way, London E1 1AA",
                    "incorporation_date": "2014-06-12",
                    "company_type": "Private limited Company",
                    "current_status": "Active",
                    "opencorporates_url": "https://opencorporates.com/companies/gb/12345678",
                }
            },
            # Row without an id — must be skipped.
            {"company": {"name": "No ID Ltd"}},
        ]
    }
}


@pytest.fixture
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


def test_parse_companies_drops_rows_without_id() -> None:
    rows = _parse_companies(SAMPLE_PAYLOAD)
    assert len(rows) == 1
    assert rows[0].opencorporates_id == "gb/12345678"
    assert rows[0].company_number == "12345678"
    assert rows[0].current_status == "Active"


def test_hash_request_ignores_api_token() -> None:
    base = {"q": "Acme Ltd", "per_page": 5, "format": "json"}
    with_key = _hash_request(SEARCH_URL, {**base, "api_token": "sekret"})
    without_key = _hash_request(SEARCH_URL, base)
    # Keyed and anonymous calls for the same query share the cache entry.
    assert with_key == without_key


@respx.mock
async def test_search_companies_parses_response(http_client: httpx.AsyncClient) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE_PAYLOAD)
    )

    client = OpenCorporatesClient(http=http_client, api_key=None)
    hits = await client.search_companies("Acme Holdings", jurisdiction_code="GB")

    assert route.called
    assert len(hits) == 1
    assert hits[0].opencorporates_id == "gb/12345678"
    assert hits[0].registered_address == "1 Acme Way, London E1 1AA"


@respx.mock
async def test_search_companies_sends_api_token_when_keyed(
    http_client: httpx.AsyncClient,
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": {"companies": []}})
    )

    client = OpenCorporatesClient(http=http_client, api_key="sekret-key")
    await client.search_companies("Acme Ltd")

    request = route.calls.last.request
    # api_token is present on the wire…
    assert "api_token=sekret-key" in str(request.url)


@respx.mock
async def test_search_companies_omits_api_token_when_anonymous(
    http_client: httpx.AsyncClient,
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": {"companies": []}})
    )

    client = OpenCorporatesClient(http=http_client, api_key=None)
    await client.search_companies("Acme Ltd")

    request = route.calls.last.request
    assert "api_token" not in str(request.url)


@respx.mock
async def test_search_companies_records_audit_without_leaking_token(
    http_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SAMPLE_PAYLOAD))

    client = OpenCorporatesClient(
        http=http_client, api_key="sekret-key", session=db_session
    )
    await client.search_companies("Acme", jurisdiction_code="GB")

    row = (
        await db_session.execute(
            select(RawFetch).where(RawFetch.source_slug == SOURCE_SLUG)
        )
    ).scalar_one()
    assert row.response_status == 200
    # Audit URL must never carry the secret.
    assert "api_token" not in row.url
    assert "q=Acme" in row.url


@respx.mock
async def test_search_companies_uses_cache_on_second_call(
    http_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE_PAYLOAD)
    )

    client = OpenCorporatesClient(
        http=http_client, api_key=None, session=db_session
    )
    first = await client.search_companies("Acme", jurisdiction_code="GB")
    second = await client.search_companies("Acme", jurisdiction_code="GB")

    assert route.call_count == 1, "second identical call should hit the cache"
    assert [c.opencorporates_id for c in first] == [c.opencorporates_id for c in second]


@respx.mock
async def test_search_companies_handles_no_matches(
    http_client: httpx.AsyncClient,
) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": {"companies": []}})
    )
    client = OpenCorporatesClient(http=http_client, api_key=None)
    hits = await client.search_companies("NoSuchCompanyZZZ")
    assert hits == []


@respx.mock
async def test_search_companies_records_audit_on_http_error(
    http_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(500, json={"error": "server error"})
    )

    client = OpenCorporatesClient(
        http=http_client, api_key=None, session=db_session
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.search_companies("Acme")

    row = (
        await db_session.execute(
            select(RawFetch).where(RawFetch.source_slug == SOURCE_SLUG)
        )
    ).scalar_one()
    assert row.response_status == 500
