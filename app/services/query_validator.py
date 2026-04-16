"""Query validator — natural language to ValidatedQuery via rules + LLM."""

from __future__ import annotations

import re

from pydantic import ValidationError

from app.core.logging import get_logger
from app.models.query import QueryRequest, QueryValidationError, ValidatedQuery
from app.services.llm import LLMClient

log = get_logger(__name__)


VAGUE_PATTERNS = (
    r"\b(any|some|all|everything|whatever|anything)\b.*\b(business|company|companies)\b",
    r"^\s*(give me|find me|get)?\s*(leads?|contacts?|companies)\s*$",
)

MIN_MEANINGFUL_TOKENS = 2


SYSTEM_PROMPT = """You extract structured lead-gen queries from natural language.

Return strictly a single JSON object with this shape:
{
  "entity_type": "<singular noun describing the business category, lowercase>",
  "city": "<city name or null>",
  "region": "<state/region/admin area or null>",
  "country": "<ISO 3166-1 alpha-2 code or null>",
  "keywords": ["<filter term>", ...],
  "confidence": <float 0.0-1.0>,
  "reason_if_low_confidence": "<string explaining what is unclear, or empty string>"
}

Rules:
- entity_type must be concrete (e.g. "restaurant", "dental clinic", "saas company").
  If the input is vague (e.g. "businesses", "companies", "leads"), set confidence <= 0.3
  and explain in reason_if_low_confidence.
- country must be a 2-letter ISO code (FR, DE, GB, IT, ES, NL, etc.) or null.
  "UK" must be normalized to "GB".
- keywords are specific filters like "vegan", "bilingual", "5-star", not the entity_type.
- Prefer higher confidence only when entity_type AND at least one of
  (city, region, country) are present.
- Output ONLY the JSON object — no prose, no code fences.
"""


class QueryValidator:
    def __init__(self, llm: LLMClient, min_confidence: float = 0.5) -> None:
        self._llm = llm
        self._min_confidence = min_confidence

    async def validate(self, req: QueryRequest) -> ValidatedQuery | QueryValidationError:
        # Fast rule-based reject before spending an LLM call.
        rule_reject = self._rule_based_reject(req.query)
        if rule_reject is not None:
            return rule_reject

        raw = await self._llm.complete_json(
            system=SYSTEM_PROMPT,
            user=req.query,
            max_tokens=512,
        )

        try:
            parsed = _coerce(raw, req.limit)
        except (ValidationError, ValueError, TypeError) as exc:
            log.warning("query_validator_bad_llm_shape", error=str(exc))
            return QueryValidationError(
                reason="Could not parse the query into a structured form.",
                suggestions=[
                    "Try a concrete entity type and a city, e.g. 'restaurants in Paris'.",
                ],
            )

        if parsed.confidence < self._min_confidence:
            return QueryValidationError(
                reason=raw.get("reason_if_low_confidence")
                or "Query is too vague to search confidently.",
                suggestions=_suggestions_for(parsed),
            )

        if not parsed.has_sufficient_location():
            return QueryValidationError(
                reason="No location detected.",
                suggestions=[
                    "Add a city or country, e.g. 'dentists in Berlin'.",
                ],
            )

        return parsed

    @staticmethod
    def _rule_based_reject(query: str) -> QueryValidationError | None:
        tokens = re.findall(r"\w+", query.lower())
        if len(tokens) < MIN_MEANINGFUL_TOKENS:
            return QueryValidationError(
                reason="Query is too short.",
                suggestions=["Describe what kind of business and where, e.g. 'cafes in Lisbon'."],
            )
        for pat in VAGUE_PATTERNS:
            if re.search(pat, query, flags=re.IGNORECASE):
                return QueryValidationError(
                    reason="Query is too generic.",
                    suggestions=[
                        "Specify an entity type, e.g. 'restaurants', 'accountants', 'hotels'.",
                        "Add a city or country.",
                    ],
                )
        return None


def _coerce(raw: dict, limit: int) -> ValidatedQuery:
    # Normalize common country mistakes before Pydantic sees it.
    country = raw.get("country")
    if isinstance(country, str):
        country = country.strip().upper()
        if country == "UK":
            country = "GB"
        if len(country) != 2:
            country = None
    else:
        country = None

    return ValidatedQuery(
        entity_type=str(raw.get("entity_type", "")).strip().lower(),
        city=(raw.get("city") or None),
        region=(raw.get("region") or None),
        country=country,
        keywords=list(raw.get("keywords") or []),
        limit=limit,
        confidence=float(raw.get("confidence", 0.0)),
    )


def _suggestions_for(q: ValidatedQuery) -> list[str]:
    tips: list[str] = []
    if not q.entity_type or q.entity_type in {"business", "company", "lead"}:
        tips.append("Use a concrete entity type (e.g. 'restaurant', 'saas company').")
    if not q.has_sufficient_location():
        tips.append("Add a location (city or country).")
    if not tips:
        tips.append("Try rephrasing with more specific terms.")
    return tips
