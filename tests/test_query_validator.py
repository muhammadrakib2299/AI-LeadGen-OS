"""Unit tests for QueryValidator. No real LLM calls — FakeLLMClient injected."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.models.query import QueryRequest, QueryValidationError, ValidatedQuery
from app.services.llm import LLMClient
from app.services.query_validator import QueryValidator


class FakeLLMClient:
    """Implements LLMClient protocol. Returns a pre-baked response per call."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def complete_json(
        self, system: str, user: str, *, model: str = "", max_tokens: int = 0
    ) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.response


def _make_validator(response: dict[str, Any]) -> tuple[QueryValidator, FakeLLMClient]:
    fake = FakeLLMClient(response)
    assert isinstance(fake, LLMClient)  # duck-type runtime check
    return QueryValidator(fake), fake


async def test_valid_query_returns_validated_query() -> None:
    validator, fake = _make_validator(
        {
            "entity_type": "restaurant",
            "city": "Paris",
            "region": None,
            "country": "FR",
            "keywords": [],
            "confidence": 0.95,
            "reason_if_low_confidence": "",
        }
    )
    result = await validator.validate(QueryRequest(query="restaurants in Paris"))
    assert isinstance(result, ValidatedQuery)
    assert result.entity_type == "restaurant"
    assert result.city == "Paris"
    assert result.country == "FR"
    assert len(fake.calls) == 1


async def test_country_uk_normalized_to_gb() -> None:
    validator, _ = _make_validator(
        {
            "entity_type": "law firm",
            "city": "London",
            "country": "UK",
            "keywords": [],
            "confidence": 0.9,
            "reason_if_low_confidence": "",
        }
    )
    result = await validator.validate(QueryRequest(query="law firms in London UK"))
    assert isinstance(result, ValidatedQuery)
    assert result.country == "GB"


async def test_too_short_query_rejected_without_llm_call() -> None:
    validator, fake = _make_validator({"confidence": 1.0})
    with pytest.raises(ValidationError):  # Pydantic min_length=3 on QueryRequest
        QueryRequest(query="x")
    # a borderline-short but valid pydantic input
    result = await validator.validate(QueryRequest(query="cafe"))
    assert isinstance(result, QueryValidationError)
    assert fake.calls == []  # LLM never called


async def test_vague_query_rejected_without_llm_call() -> None:
    validator, fake = _make_validator({"confidence": 1.0})
    result = await validator.validate(QueryRequest(query="give me some companies"))
    assert isinstance(result, QueryValidationError)
    assert "generic" in result.reason.lower()
    assert fake.calls == []


async def test_low_confidence_returns_error_with_suggestions() -> None:
    validator, _ = _make_validator(
        {
            "entity_type": "business",
            "city": None,
            "country": None,
            "keywords": [],
            "confidence": 0.2,
            "reason_if_low_confidence": "entity_type too broad",
        }
    )
    result = await validator.validate(QueryRequest(query="interesting businesses"))
    assert isinstance(result, QueryValidationError)
    assert "too broad" in result.reason or "too vague" in result.reason.lower()
    assert result.suggestions


async def test_missing_location_rejected() -> None:
    validator, _ = _make_validator(
        {
            "entity_type": "saas company",
            "city": None,
            "country": None,
            "keywords": ["B2B"],
            "confidence": 0.8,
            "reason_if_low_confidence": "",
        }
    )
    result = await validator.validate(QueryRequest(query="B2B SaaS companies"))
    assert isinstance(result, QueryValidationError)
    assert "location" in result.reason.lower()


async def test_bad_shape_from_llm_returns_validation_error() -> None:
    validator, _ = _make_validator(
        {
            "entity_type": "",
            "confidence": "not-a-number",  # forces pydantic error
        }
    )
    result = await validator.validate(QueryRequest(query="dentists in Berlin"))
    assert isinstance(result, QueryValidationError)
