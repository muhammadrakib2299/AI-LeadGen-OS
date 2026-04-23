"""Tests for the embeddings client + entity-to-text mapping."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
import respx

from app.services.embeddings import (
    DEFAULT_DIM,
    OPENAI_BASE_URL,
    OPENAI_EMBEDDINGS_PATH,
    EmbeddingsClient,
    OpenAIEmbeddings,
    entity_to_embed_text,
)


@dataclass
class _FakeEntity:
    name: str = ""
    city: str = ""
    country: str = ""
    category: str = ""
    email: str = "secret@example.com"  # PII — must NOT appear in embed text
    phone: str = "+44 1234 567890"     # PII — must NOT appear in embed text
    address: str = "1 Secret Lane"     # PII — must NOT appear in embed text


def test_entity_to_text_joins_non_pii_fields() -> None:
    e = _FakeEntity(
        name="Acme Bakery", city="Lyon", country="FR", category="bakery"
    )
    assert entity_to_embed_text(e) == "Acme Bakery | Lyon | FR | bakery"


def test_entity_to_text_omits_empty_fields() -> None:
    e = _FakeEntity(name="Acme")
    assert entity_to_embed_text(e) == "Acme"


def test_entity_to_text_excludes_pii_fields() -> None:
    e = _FakeEntity(name="Acme", city="Paris", category="cafe")
    text = entity_to_embed_text(e)
    # Sanity guard against a regression that adds email/phone/address back in.
    assert "secret@example.com" not in text
    assert "1234 567890" not in text
    assert "Secret Lane" not in text


async def test_openai_embed_empty_input_returns_empty() -> None:
    client = OpenAIEmbeddings(api_key="sk-test")
    assert await client.embed([]) == []


@respx.mock
async def test_openai_embed_sends_bearer_and_returns_vectors() -> None:
    payload = {
        "data": [
            {"index": 0, "embedding": [0.1] * DEFAULT_DIM},
            {"index": 1, "embedding": [0.2] * DEFAULT_DIM},
        ],
        "model": "text-embedding-3-small",
    }
    route = respx.post(OPENAI_BASE_URL + OPENAI_EMBEDDINGS_PATH).mock(
        return_value=httpx.Response(200, json=payload)
    )

    async with httpx.AsyncClient() as http:
        client = OpenAIEmbeddings(api_key="sk-test", http=http)
        vectors = await client.embed(["one", "two"])

    assert len(vectors) == 2
    assert all(len(v) == DEFAULT_DIM for v in vectors)
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer sk-test"
    body = req.content.decode()
    assert '"input":["one","two"]' in body


@respx.mock
async def test_openai_embed_preserves_order_when_provider_returns_shuffled() -> None:
    # The API spec says "data" is in input order but does not strictly
    # promise it — explicit sort by `index` keeps us safe if it ever
    # changes upstream.
    payload = {
        "data": [
            {"index": 1, "embedding": [0.2] * 4},
            {"index": 0, "embedding": [0.1] * 4},
        ],
        "model": "text-embedding-3-small",
    }
    respx.post(OPENAI_BASE_URL + OPENAI_EMBEDDINGS_PATH).mock(
        return_value=httpx.Response(200, json=payload)
    )
    async with httpx.AsyncClient() as http:
        client = OpenAIEmbeddings(api_key="sk-test", http=http)
        vectors = await client.embed(["a", "b"])
    assert vectors[0][0] == pytest.approx(0.1)  # for "a"
    assert vectors[1][0] == pytest.approx(0.2)  # for "b"


@respx.mock
async def test_openai_embed_raises_on_4xx() -> None:
    respx.post(OPENAI_BASE_URL + OPENAI_EMBEDDINGS_PATH).mock(
        return_value=httpx.Response(401, text="invalid_api_key")
    )
    async with httpx.AsyncClient() as http:
        client = OpenAIEmbeddings(api_key="sk-bad", http=http)
        with pytest.raises(httpx.HTTPStatusError):
            await client.embed(["x"])


def test_openai_embeddings_satisfies_protocol() -> None:
    """Catch a rename or shape break at static-config time, not runtime."""
    assert isinstance(OpenAIEmbeddings(api_key="sk"), EmbeddingsClient)
