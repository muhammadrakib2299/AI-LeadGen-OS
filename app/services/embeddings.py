"""Text embeddings for AI Ask Mode v2.

Direct httpx → OpenAI's `/v1/embeddings`. No SDK dependency — same
pattern as the HubSpot / Pipedrive / Sheets clients. We pass the
provider behind a Protocol so tests inject a deterministic fake.

Default model: text-embedding-3-small (1536 dim, $0.02 / 1M tokens).
Switching providers requires updating both:
- this file's DEFAULT_DIM
- alembic migration a6d2f8b1e094 (vector column dim)
- app/db/models.py ENTITY_EMBEDDING_DIM

GDPR note: only non-PII text is embedded. See `entity_to_embed_text`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_EMBEDDINGS_PATH = "/embeddings"
DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIM = 1536


@runtime_checkable
class EmbeddingsClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text. Must preserve order."""
        ...


def entity_to_embed_text(entity: Any) -> str:
    """Build the embedding payload from an Entity.

    Joined with " | " so the encoder sees the fields as distinct
    semantic units. PII (email, phone, address) is deliberately omitted —
    a third-party embeddings API never sees a contactable identifier.
    """
    parts = [
        getattr(entity, "name", "") or "",
        getattr(entity, "city", "") or "",
        getattr(entity, "country", "") or "",
        getattr(entity, "category", "") or "",
    ]
    return " | ".join(p for p in parts if p)


class OpenAIEmbeddings:
    """Production embeddings client. Don't instantiate in tests."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._http = http
        self._owns_http = http is None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._http or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(
                OPENAI_BASE_URL + OPENAI_EMBEDDINGS_PATH,
                json={"model": self._model, "input": texts},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            # Response shape: {"data": [{"embedding": [...], "index": 0}, ...]}
            # Sort by index to preserve caller-supplied order.
            rows = sorted(data["data"], key=lambda r: r["index"])
            return [row["embedding"] for row in rows]
        finally:
            if self._owns_http:
                await client.aclose()
