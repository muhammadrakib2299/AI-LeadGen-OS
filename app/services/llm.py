"""Thin Anthropic wrapper with a Protocol seam for testing.

Design: the rest of the app depends on `LLMClient` (Protocol), not the Anthropic SDK.
Tests pass in a FakeLLMClient instance; production wires AnthropicClient.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol, runtime_checkable

from anthropic import APIError, AsyncAnthropic

from app.core.circuit import CircuitBreaker
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


LLMTier = Literal["fast", "premium"]

# Per overview.md §5: Haiku / gpt-4o-mini as default cheap tier.
# Premium escalation uses Sonnet 4.6 — still cheaper than Opus, and the
# quality bump is usually enough to rescue an ambiguous query.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
PREMIUM_MODEL = "claude-sonnet-4-6"

TIER_TO_MODEL: dict[LLMTier, str] = {
    "fast": DEFAULT_MODEL,
    "premium": PREMIUM_MODEL,
}


@runtime_checkable
class LLMClient(Protocol):
    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
        tier: LLMTier = "fast",
    ) -> dict[str, Any]:
        """Return the JSON object the model emitted. Raises on non-JSON output.

        `tier` selects a model family. Explicit `model=` wins over `tier=`,
        so existing callers that already pass `model=` keep their behavior.
        """
        ...


_ANTHROPIC_BREAKER = CircuitBreaker(
    name="anthropic",
    failure_threshold=5,
    cooldown_s=60.0,
    expected_exceptions=(APIError,),
)


class AnthropicClient:
    """Production LLM client. Do not instantiate in tests."""

    def __init__(
        self, api_key: str | None = None, breaker: CircuitBreaker | None = None
    ) -> None:
        key = api_key or get_settings().anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Real LLM calls are disabled.")
        self._client = AsyncAnthropic(api_key=key)
        self._breaker = breaker or _ANTHROPIC_BREAKER

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
        tier: LLMTier = "fast",
    ) -> dict[str, Any]:
        # Tier hint picks a model family. An explicit model= arg always wins —
        # callers that already specify a model don't get their choice overridden.
        effective_model = model if model != DEFAULT_MODEL else TIER_TO_MODEL[tier]

        async def _do_call() -> Any:
            return await self._client.messages.create(
                model=effective_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )

        resp = await self._breaker.call(_do_call)
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        try:
            return json.loads(_extract_json(text))
        except json.JSONDecodeError as exc:
            log.warning("llm_non_json_response", raw=text[:200], model=effective_model)
            raise ValueError(f"LLM returned non-JSON: {text[:200]}") from exc


def _extract_json(text: str) -> str:
    """Pull the first {...} block out of an LLM response.

    Haiku usually emits clean JSON, but defensively strip code-fence noise.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].lstrip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return stripped
    return stripped[start : end + 1]
