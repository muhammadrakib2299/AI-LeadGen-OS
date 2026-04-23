"""Tests for Pipedrive person mapping + export."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import respx

from app.services.pipedrive import (
    DEFAULT_BASE_URL,
    PERSONS_ENDPOINT,
    base_url_for,
    entity_to_person_payload,
    export_persons,
)


@dataclass
class _FakeEntity:
    name: str | None = "Acme Ltd"
    email: str | None = None
    phone: str | None = None


def test_mapping_skips_entities_without_email() -> None:
    assert entity_to_person_payload(_FakeEntity(name="Acme", email=None)) is None


def test_mapping_includes_email_and_phone_arrays() -> None:
    e = _FakeEntity(name="Acme", email="hello@acme.example", phone="+44 20 1234 5678")
    payload = entity_to_person_payload(e)
    assert payload == {
        "name": "Acme",
        "email": [{"value": "hello@acme.example", "primary": True, "label": "work"}],
        "phone": [{"value": "+44 20 1234 5678", "primary": True, "label": "work"}],
    }


def test_mapping_omits_phone_when_absent() -> None:
    payload = entity_to_person_payload(_FakeEntity(name="Acme", email="x@acme.example"))
    assert payload is not None
    assert "phone" not in payload


def test_base_url_uses_global_when_no_company_domain() -> None:
    assert base_url_for(None) == DEFAULT_BASE_URL


def test_base_url_uses_company_subdomain_when_set() -> None:
    assert base_url_for("acme") == "https://acme.pipedrive.com/v1"


@respx.mock
async def test_export_sends_api_token_query_param() -> None:
    entities = [_FakeEntity(name="A", email="a@x.example")]
    route = respx.post(DEFAULT_BASE_URL + PERSONS_ENDPOINT).mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": 1}})
    )
    async with httpx.AsyncClient() as http:
        result = await export_persons("tkn-abc", entities, http=http)
    assert result.attempted == 1
    assert result.created == 1
    req = route.calls.last.request
    assert req.url.params["api_token"] == "tkn-abc"


@respx.mock
async def test_export_uses_company_subdomain_when_provided() -> None:
    entities = [_FakeEntity(name="A", email="a@x.example")]
    route = respx.post("https://acme.pipedrive.com/v1" + PERSONS_ENDPOINT).mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": 1}})
    )
    async with httpx.AsyncClient() as http:
        result = await export_persons(
            "tkn", entities, company_domain="acme", http=http
        )
    assert result.created == 1
    assert route.call_count == 1


@respx.mock
async def test_export_continues_after_per_row_error() -> None:
    entities = [
        _FakeEntity(name="ok", email="ok@x.example"),
        _FakeEntity(name="bad", email="bad@x.example"),
        _FakeEntity(name="ok2", email="ok2@x.example"),
    ]
    responses = [
        httpx.Response(201, json={"success": True, "data": {"id": 1}}),
        httpx.Response(401, text="invalid token"),
        httpx.Response(201, json={"success": True, "data": {"id": 3}}),
    ]
    respx.post(DEFAULT_BASE_URL + PERSONS_ENDPOINT).mock(side_effect=responses)
    async with httpx.AsyncClient() as http:
        result = await export_persons("tkn", entities, http=http)
    assert result.attempted == 3
    assert result.created == 2
    assert len(result.errors) == 1
    assert "401" in result.errors[0]


@respx.mock
async def test_export_posts_one_request_per_entity() -> None:
    entities = [_FakeEntity(name=f"e{i}", email=f"e{i}@x.example") for i in range(5)]
    route = respx.post(DEFAULT_BASE_URL + PERSONS_ENDPOINT).mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": 1}})
    )
    async with httpx.AsyncClient() as http:
        result = await export_persons("tkn", entities, http=http)
    assert route.call_count == 5
    assert result.created == 5


def test_export_noop_without_emails() -> None:
    entities = [_FakeEntity(name="no-email"), _FakeEntity(name="also-no-email")]
    result = asyncio.run(export_persons("tkn", entities))
    assert result.attempted == 0
    assert result.created == 0
    assert result.errors == []
