"""Tests for the rule-based query parser (zero-LLM fast path)."""

from __future__ import annotations

from app.models.query import ValidatedQuery
from app.services.query_rules import try_rule_parse


def _parse(q: str) -> ValidatedQuery | None:
    return try_rule_parse(q, limit=50)


def test_entity_in_city_hits_city_map() -> None:
    res = _parse("restaurants in Paris")
    assert res is not None
    assert res.entity_type == "restaurants"
    assert res.city == "Paris"
    assert res.country == "FR"
    assert res.confidence == 0.75


def test_entity_in_city_country_word() -> None:
    res = _parse("dentists in Berlin Germany")
    assert res is not None
    assert res.entity_type == "dentists"
    assert res.city == "Berlin"
    assert res.country == "DE"


def test_entity_in_city_comma_country() -> None:
    res = _parse("bakeries in Lisbon, Portugal")
    assert res is not None
    assert res.city == "Lisbon"
    assert res.country == "PT"


def test_entity_in_city_comma_country_code() -> None:
    res = _parse("vegan restaurants in Paris, FR")
    assert res is not None
    assert res.entity_type == "vegan restaurants"
    assert res.city == "Paris"
    assert res.country == "FR"


def test_uk_normalizes_to_gb() -> None:
    res = _parse("law firms in London, UK")
    assert res is not None
    assert res.country == "GB"


def test_england_normalizes_to_gb() -> None:
    res = _parse("pubs in Manchester, England")
    assert res is not None
    assert res.country == "GB"


def test_unicode_city() -> None:
    res = _parse("restaurants in Düsseldorf")
    assert res is not None
    assert res.city == "Düsseldorf"
    assert res.country == "DE"


def test_top_n_prefix_is_stripped() -> None:
    res = _parse("top 10 coffee shops in Berlin")
    assert res is not None
    assert res.entity_type == "coffee shops"
    assert res.city == "Berlin"


def test_vague_entity_type_rejected() -> None:
    # "companies in Paris" is too vague — fall through to LLM.
    assert _parse("companies in Paris") is None
    assert _parse("businesses in Berlin") is None


def test_unknown_city_alone_returns_none() -> None:
    # "Foobarville" isn't in the city map and no country trails it.
    assert _parse("restaurants in Foobarville") is None


def test_no_in_keyword_returns_none() -> None:
    assert _parse("just some random query") is None


def test_country_only_location_still_resolves() -> None:
    # Match "saas startups in Germany" → country DE, no city
    res = _parse("saas startups in Germany")
    assert res is not None
    assert res.city is None
    assert res.country == "DE"


def test_titlecase_multi_word_city() -> None:
    res = _parse("museums in The Hague")
    assert res is not None
    assert res.city == "The Hague"
    assert res.country == "NL"


def test_uses_city_map_even_with_comma_country_unknown() -> None:
    # Country clause unrecognized, but city is known → still emit country
    res = _parse("cafes in Lisbon, Atlantis")
    assert res is not None
    assert res.city == "Lisbon"
    assert res.country == "PT"
