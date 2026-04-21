"""Tests for HubSpot contact mapping + batch export."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import respx

from app.services.hubspot import (
    CONTACTS_BATCH_CREATE,
    HUBSPOT_BASE_URL,
    entity_to_contact_properties,
    export_contacts,
)


@dataclass
class _FakeEntity:
    name: str | None = "Acme Ltd"
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    city: str | None = None
    country: str | None = None


def test_mapping_skips_entities_without_email() -> None:
    e = _FakeEntity(name="Acme", email=None)
    assert entity_to_contact_properties(e) is None


def test_mapping_includes_only_set_fields() -> None:
    e = _FakeEntity(
        name="Acme",
        email="hello@acme.example",
        phone="+44 20 1234 5678",
        website="https://acme.example",
        city="London",
        country="GB",
    )
    props = entity_to_contact_properties(e)
    assert props == {
        "email": "hello@acme.example",
        "company": "Acme",
        "phone": "+44 20 1234 5678",
        "website": "https://acme.example",
        "city": "London",
        "country": "GB",
    }


@respx.mock
async def test_export_contacts_posts_bearer_token() -> None:
    entities = [_FakeEntity(name="A", email="a@x.example")]
    route = respx.post(HUBSPOT_BASE_URL + CONTACTS_BATCH_CREATE).mock(
        return_value=httpx.Response(201, json={"results": [{"id": "1"}]})
    )
    async with httpx.AsyncClient() as http:
        result = await export_contacts("pat-abc", entities, http=http)
    assert result.attempted == 1
    assert result.created == 1
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer pat-abc"


@respx.mock
async def test_export_contacts_reports_errors_per_batch() -> None:
    entities = [_FakeEntity(name=f"{i}", email=f"e{i}@x.example") for i in range(3)]
    respx.post(HUBSPOT_BASE_URL + CONTACTS_BATCH_CREATE).mock(
        return_value=httpx.Response(401, text="invalid token")
    )
    async with httpx.AsyncClient() as http:
        result = await export_contacts("bad", entities, http=http)
    assert result.attempted == 3
    assert result.created == 0
    assert result.errors and "401" in result.errors[0]


@respx.mock
async def test_export_contacts_chunks_into_batches_of_100() -> None:
    # 150 entities should produce 2 POSTs (100 + 50).
    entities = [
        _FakeEntity(name=f"e{i}", email=f"e{i}@x.example") for i in range(150)
    ]
    route = respx.post(HUBSPOT_BASE_URL + CONTACTS_BATCH_CREATE).mock(
        return_value=httpx.Response(201, json={"results": []})
    )
    async with httpx.AsyncClient() as http:
        result = await export_contacts("pat", entities, http=http)
    assert route.call_count == 2
    assert result.attempted == 150


def test_export_contacts_noop_without_emails() -> None:
    import asyncio

    entities = [_FakeEntity(name="no-email")]
    result = asyncio.run(export_contacts("pat", entities))
    assert result.attempted == 0
    assert result.created == 0
