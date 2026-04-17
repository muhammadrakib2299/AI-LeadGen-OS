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
        self.tiers: list[str] = []

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = "",
        max_tokens: int = 0,
        tier: str = "fast",
    ) -> dict[str, Any]:
        self.calls.append((system, user))
        self.tiers.append(tier)
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
    # Use a query the rule parser can't solve so the LLM path runs.
    result = await validator.validate(
        QueryRequest(query="the coolest little restaurant around Paris somewhere")
    )
    assert isinstance(result, ValidatedQuery)
    assert result.entity_type == "restaurant"
    assert result.city == "Paris"
    assert result.country == "FR"
    assert len(fake.calls) == 1


async def test_rule_fast_path_skips_llm_entirely() -> None:
    validator, fake = _make_validator(
        {"entity_type": "never", "confidence": 0.99}  # LLM would return this — never called
    )
    result = await validator.validate(QueryRequest(query="restaurants in Paris"))
    assert isinstance(result, ValidatedQuery)
    assert result.entity_type == "restaurants"
    assert result.city == "Paris"
    assert result.country == "FR"
    # The LLM was never consulted.
    assert fake.calls == []


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
    # Use a query the rule parser can't solve so we exercise the LLM path.
    result = await validator.validate(QueryRequest(query="some dental clinics near my area"))
    assert isinstance(result, QueryValidationError)


class _TieredFakeLLM:
    """Fake that returns different responses for fast vs premium tiers."""

    def __init__(self, fast: dict[str, Any], premium: dict[str, Any]) -> None:
        self._responses = {"fast": fast, "premium": premium}
        self.tiers: list[str] = []

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = "",
        max_tokens: int = 0,
        tier: str = "fast",
    ) -> dict[str, Any]:
        self.tiers.append(tier)
        return self._responses[tier]


async def test_escalates_to_premium_when_fast_confidence_is_low() -> None:
    fake = _TieredFakeLLM(
        fast={
            "entity_type": "restaurant",
            "city": "Paris",
            "country": "FR",
            "keywords": [],
            "confidence": 0.35,  # below min_confidence=0.5
            "reason_if_low_confidence": "ambiguous",
        },
        premium={
            "entity_type": "restaurant",
            "city": "Paris",
            "country": "FR",
            "keywords": [],
            "confidence": 0.88,
            "reason_if_low_confidence": "",
        },
    )
    validator = QueryValidator(fake)  # type: ignore[arg-type]
    result = await validator.validate(QueryRequest(query="someplace in Paris maybe"))
    assert isinstance(result, ValidatedQuery)
    assert result.confidence == pytest.approx(0.88)
    assert fake.tiers == ["fast", "premium"]


async def test_no_escalation_when_fast_already_confident() -> None:
    fake = _TieredFakeLLM(
        fast={
            "entity_type": "dentist",
            "city": "Berlin",
            "country": "DE",
            "keywords": [],
            "confidence": 0.92,
            "reason_if_low_confidence": "",
        },
        premium={"confidence": 0.0},  # should never be called
    )
    validator = QueryValidator(fake)  # type: ignore[arg-type]
    # Free-form query — rule parser won't match, so the LLM path runs.
    result = await validator.validate(QueryRequest(query="cool dentist practices near me"))
    assert isinstance(result, ValidatedQuery)
    assert fake.tiers == ["fast"]


async def test_escalation_still_rejects_when_premium_also_low() -> None:
    fake = _TieredFakeLLM(
        fast={
            "entity_type": "business",
            "city": None,
            "country": None,
            "keywords": [],
            "confidence": 0.2,
            "reason_if_low_confidence": "very vague",
        },
        premium={
            "entity_type": "business",
            "city": None,
            "country": None,
            "keywords": [],
            "confidence": 0.25,  # slightly higher but still below threshold
            "reason_if_low_confidence": "still vague",
        },
    )
    validator = QueryValidator(fake)  # type: ignore[arg-type]
    result = await validator.validate(QueryRequest(query="cool businesses somewhere"))
    assert isinstance(result, QueryValidationError)
    assert fake.tiers == ["fast", "premium"]


async def test_escalation_disabled_does_not_call_premium() -> None:
    fake = _TieredFakeLLM(
        fast={
            "entity_type": "restaurant",
            "city": "Paris",
            "country": "FR",
            "keywords": [],
            "confidence": 0.3,
            "reason_if_low_confidence": "ambiguous",
        },
        premium={"confidence": 1.0},  # should never be called
    )
    validator = QueryValidator(fake, escalate_on_low_confidence=False)  # type: ignore[arg-type]
    # Free-form query — rule parser can't solve; the LLM path is exercised.
    result = await validator.validate(QueryRequest(query="some kind of place around Paris"))
    assert isinstance(result, QueryValidationError)
    assert fake.tiers == ["fast"]


async def test_no_escalation_when_no_entity_type() -> None:
    # Premium can't save a query that doesn't even name an entity type —
    # skip the escalation and save the call.
    fake = _TieredFakeLLM(
        fast={
            "entity_type": "",
            "city": None,
            "country": None,
            "keywords": [],
            "confidence": 0.1,
            "reason_if_low_confidence": "nothing to parse",
        },
        premium={"confidence": 1.0},
    )
    validator = QueryValidator(fake)  # type: ignore[arg-type]
    result = await validator.validate(QueryRequest(query="hmm okay then"))
    assert isinstance(result, QueryValidationError)
    assert fake.tiers == ["fast"]
