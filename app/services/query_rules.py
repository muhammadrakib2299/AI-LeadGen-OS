"""Rule-based query parser — zero-LLM fast path for common queries.

Recognized shapes (case-insensitive):

- "<entity_type> in <city>"                  → restaurants in Paris
- "<entity_type> in <city>, <country>"       → restaurants in Paris, France
- "<entity_type> in <city> <country>"        → dentists in Berlin Germany
- "<adj...> <entity_type> in <city>, <cc>"   → vegan restaurants in Paris, FR

When a pattern matches with a known location we emit a ValidatedQuery with
confidence=0.75. The validator skips the LLM entirely for those — one free
Haiku call saved per discovery job. Queries that don't match fall through
to the LLM path (or the rule-based reject filter, whichever fires first).
"""

from __future__ import annotations

import re

from app.models.query import ValidatedQuery

# Alpha-2 codes for the markets we actually serve + common user typings.
# Small and hand-curated is fine here; adding countries is a one-liner.
COUNTRY_NAME_TO_CODE: dict[str, str] = {
    # EU / UK first (our wheelhouse)
    "france": "FR",
    "germany": "DE",
    "deutschland": "DE",
    "italy": "IT",
    "italia": "IT",
    "spain": "ES",
    "españa": "ES",
    "portugal": "PT",
    "netherlands": "NL",
    "holland": "NL",
    "belgium": "BE",
    "austria": "AT",
    "switzerland": "CH",
    "ireland": "IE",
    "poland": "PL",
    "sweden": "SE",
    "denmark": "DK",
    "norway": "NO",
    "finland": "FI",
    "czech republic": "CZ",
    "czechia": "CZ",
    "greece": "GR",
    "romania": "RO",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    # Other common markets — kept small on purpose
    "united states": "US",
    "usa": "US",
    "us": "US",
    "canada": "CA",
    "australia": "AU",
}

# Common city → ISO alpha-2 mapping for location-without-country phrasing
# ("bakeries in Lisbon"). Only cities unambiguous enough to bet on.
CITY_TO_COUNTRY: dict[str, str] = {
    "paris": "FR",
    "lyon": "FR",
    "marseille": "FR",
    "toulouse": "FR",
    "nice": "FR",
    "bordeaux": "FR",
    "berlin": "DE",
    "munich": "DE",
    "münchen": "DE",
    "hamburg": "DE",
    "cologne": "DE",
    "köln": "DE",
    "frankfurt": "DE",
    "düsseldorf": "DE",
    "dusseldorf": "DE",
    "stuttgart": "DE",
    "rome": "IT",
    "roma": "IT",
    "milan": "IT",
    "milano": "IT",
    "naples": "IT",
    "turin": "IT",
    "torino": "IT",
    "florence": "IT",
    "firenze": "IT",
    "madrid": "ES",
    "barcelona": "ES",
    "valencia": "ES",
    "seville": "ES",
    "sevilla": "ES",
    "lisbon": "PT",
    "lisboa": "PT",
    "porto": "PT",
    "amsterdam": "NL",
    "rotterdam": "NL",
    "the hague": "NL",
    "brussels": "BE",
    "antwerp": "BE",
    "vienna": "AT",
    "wien": "AT",
    "zurich": "CH",
    "geneva": "CH",
    "dublin": "IE",
    "warsaw": "PL",
    "kraków": "PL",
    "krakow": "PL",
    "stockholm": "SE",
    "copenhagen": "DK",
    "oslo": "NO",
    "helsinki": "FI",
    "prague": "CZ",
    "athens": "GR",
    "bucharest": "RO",
    "london": "GB",
    "manchester": "GB",
    "birmingham": "GB",
    "edinburgh": "GB",
    "glasgow": "GB",
    "new york": "US",
    "san francisco": "US",
    "los angeles": "US",
}

# Queries referencing only these bare nouns are too vague — same list as the
# LLM prompt's negative examples. Rule parser refuses them so we don't emit
# a high-confidence ValidatedQuery for garbage.
VAGUE_ENTITY_TYPES = frozenset(
    {"business", "businesses", "company", "companies", "lead", "leads", "contact", "contacts"}
)

# "<whatever> in <location>" — location runs to the end of the string.
_IN_PATTERN = re.compile(
    r"^\s*(?P<entity>.+?)\s+in\s+(?P<location>.+?)\s*$",
    flags=re.IGNORECASE,
)


def try_rule_parse(query: str, *, limit: int) -> ValidatedQuery | None:
    """Return a ValidatedQuery if `query` matches a known pattern, else None.

    No LLM involved. Confidence is fixed at 0.75 — above the default
    min_confidence (0.5) so the validator accepts it, but below LLM high-
    confidence scores so escalation still feels meaningful later.
    """
    m = _IN_PATTERN.match(query)
    if m is None:
        return None

    raw_entity = _normalize_entity(m.group("entity"))
    if not raw_entity or raw_entity in VAGUE_ENTITY_TYPES:
        return None

    city, country = _split_location(m.group("location"))
    if city is None and country is None:
        return None

    return ValidatedQuery(
        entity_type=raw_entity,
        city=city,
        region=None,
        country=country,
        keywords=[],
        limit=limit,
        confidence=0.75,
    )


def _normalize_entity(raw: str) -> str:
    # Collapse whitespace, drop trailing punctuation, lowercase.
    cleaned = re.sub(r"\s+", " ", raw).strip(" .,:;").lower()
    # Heuristic: strip a leading quantifier ("top 10", "best 5") so the
    # entity_type itself stays clean.
    cleaned = re.sub(r"^(?:top|best|the top|the best)\s+\d+\s+", "", cleaned)
    return cleaned[:64]


def _split_location(raw: str) -> tuple[str | None, str | None]:
    """Pull (city, country_code) out of a free-text location string."""
    text = raw.strip(" .,:;")
    if not text:
        return None, None

    # "Paris, France" or "Paris, FR"
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        city = parts[0] or None
        country_code = _country_code(parts[1]) if len(parts) > 1 else None
        if city and _city_to_country(city) and country_code is None:
            country_code = _city_to_country(city)
        return _titlecase_city(city), country_code

    # "Berlin Germany" — trailing country word(s)
    lower = text.lower()
    for name, code in sorted(COUNTRY_NAME_TO_CODE.items(), key=lambda x: -len(x[0])):
        if lower.endswith(" " + name):
            city_part = text[: -(len(name) + 1)].strip()
            return _titlecase_city(city_part or None), code

    # Just a city — look it up
    city_code = _city_to_country(text)
    if city_code is not None:
        return _titlecase_city(text), city_code

    # Standalone country code or name
    lone = _country_code(text)
    if lone is not None:
        return None, lone

    return None, None


def _country_code(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    # Check the name map first — "UK" is alpha-2-shaped but the user means GB.
    mapped = COUNTRY_NAME_TO_CODE.get(value)
    if mapped is not None:
        return mapped
    if len(value) == 2 and value.isalpha():
        return value.upper()
    return None


def _city_to_country(city: str | None) -> str | None:
    if not city:
        return None
    return CITY_TO_COUNTRY.get(city.strip().lower())


def _titlecase_city(city: str | None) -> str | None:
    if not city:
        return None
    # Respect multi-word cities like "The Hague" — title-case each word.
    return " ".join(part.capitalize() for part in city.strip().split())
