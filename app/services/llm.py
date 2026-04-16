"""Thin Anthropic wrapper with a Protocol seam for testing.

Design: the rest of the app depends on `LLMClient` (Protocol), not the Anthropic SDK.
Tests pass in a FakeLLMClient instance; production wires AnthropicClient.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from anthropic import AsyncAnthropic

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


# Per overview.md §5: Haiku / gpt-4o-mini as default cheap tier.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@runtime_checkable
class LLMClient(Protocol):
    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Return the JSON object the model emitted. Raises on non-JSON output."""
        ...


class AnthropicClient:
    """Production LLM client. Do not instantiate in tests."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or get_settings().anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Real LLM calls are disabled.")
        self._client = AsyncAnthropic(api_key=key)

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        resp = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        try:
            return json.loads(_extract_json(text))
        except json.JSONDecodeError as exc:
            log.warning("llm_non_json_response", raw=text[:200])
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
