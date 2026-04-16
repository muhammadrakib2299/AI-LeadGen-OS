"""Google Places API (New) client.

When a SQLAlchemy session is supplied:
- Every call writes an audit row to `raw_fetches` (compliance.md §7).
- The client consults `raw_fetches` for a <30d fresh cached response with the
  same content hash before spending a new API call.

When no session is supplied the client only does HTTP — useful for unit tests
that purely verify request shape/response parsing via respx.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import RawFetch
from app.models.places import Place

log = get_logger(__name__)


PLACES_BASE_URL = "https://places.googleapis.com/v1"
SOURCE_SLUG = "google_places"
CACHE_TTL = timedelta(days=30)
LEGAL_BASIS = "legitimate_interest"

# Conservative estimates per Places API (New) pricing (SKU "Pro" tier).
TEXT_SEARCH_COST_USD = 0.032

TEXT_SEARCH_FIELDS = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.addressComponents",
        "places.location",
        "places.types",
        "places.primaryType",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.rating",
        "places.userRatingCount",
    )
)


class PlacesClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.google_places_api_key
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._session = session
        self._owns_http = http is None

    async def __aenter__(self) -> PlacesClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def text_search(
        self,
        query: str,
        *,
        region_code: str | None = None,
        language_code: str = "en",
        max_results: int = 20,
        job_id: UUID | None = None,
    ) -> list[Place]:
        body: dict[str, Any] = {
            "textQuery": query,
            "maxResultCount": max_results,
            "languageCode": language_code,
        }
        if region_code:
            body["regionCode"] = region_code

        url = f"{PLACES_BASE_URL}/places:searchText"
        content_hash = _hash_request(url, body)

        cached = await self._cache_lookup(content_hash)
        if cached is not None:
            log.info("places_cache_hit", query=query)
            return _parse_places(cached)

        if not self._api_key:
            raise RuntimeError("GOOGLE_PLACES_API_KEY is not set; cannot make live Places request.")

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": TEXT_SEARCH_FIELDS,
        }

        started = time.monotonic()
        response = await self._http.post(url, headers=headers, json=body)
        duration_ms = int((time.monotonic() - started) * 1000)

        payload = _try_json(response)
        await self._record_fetch(
            url=url,
            method="POST",
            response_status=response.status_code,
            bytes_fetched=len(response.content),
            duration_ms=duration_ms,
            content_hash=content_hash,
            payload=payload,
            cost_usd=TEXT_SEARCH_COST_USD,
            job_id=job_id,
        )

        response.raise_for_status()
        return _parse_places(payload or {})

    async def _cache_lookup(self, content_hash: str) -> dict[str, Any] | None:
        if self._session is None:
            return None
        cutoff = datetime.now(UTC) - CACHE_TTL
        stmt = (
            select(RawFetch)
            .where(
                RawFetch.content_hash == content_hash,
                RawFetch.source_slug == SOURCE_SLUG,
                RawFetch.response_status == 200,
                RawFetch.created_at >= cutoff,
            )
            .order_by(RawFetch.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.payload if row is not None else None

    async def _record_fetch(
        self,
        *,
        url: str,
        method: str,
        response_status: int,
        bytes_fetched: int,
        duration_ms: int,
        content_hash: str,
        payload: dict[str, Any] | None,
        cost_usd: float,
        job_id: UUID | None,
    ) -> None:
        if self._session is None:
            return
        self._session.add(
            RawFetch(
                job_id=job_id,
                source_slug=SOURCE_SLUG,
                url=url,
                method=method,
                legal_basis=LEGAL_BASIS,
                response_status=response_status,
                bytes_fetched=bytes_fetched,
                duration_ms=duration_ms,
                content_hash=content_hash,
                payload=payload,
                cost_usd=cost_usd,
            )
        )
        await self._session.flush()


def _hash_request(url: str, body: dict[str, Any]) -> str:
    blob = json.dumps({"url": url, "body": body}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _try_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_places(payload: dict[str, Any]) -> list[Place]:
    raw = payload.get("places") or []
    return [Place.model_validate(p) for p in raw]
