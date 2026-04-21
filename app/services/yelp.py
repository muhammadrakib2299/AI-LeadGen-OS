"""Yelp Fusion /businesses/search client.

Mirrors the OpenCorporates client pattern (audit logging, cache-via-raw-fetches,
circuit breaker) with one compliance-driven twist:

    Yelp Fusion ToS forbids storing API-returned data beyond 24 hours except
    the Yelp business ID. We therefore use a 24h cache TTL and the retention
    sweep nulls out `raw_fetches.payload` rows older than 24h whose
    source_slug == "yelp".

API docs: https://docs.developer.yelp.com/reference/v3_business_search
Rate limits: 5,000 requests/day on the free tier; ~1 RPS burst.
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

from app.core.circuit import CircuitBreaker
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import RawFetch
from app.models.yelp import YelpBusiness

log = get_logger(__name__)

YELP_BASE_URL = "https://api.yelp.com/v3"
SOURCE_SLUG = "yelp"
CACHE_TTL = timedelta(hours=24)  # Capped by Yelp ToS (see module docstring).
LEGAL_BASIS = "legitimate_interest"

# Yelp's free tier doesn't meter per call, but we still log a nominal cost
# so per-job cost reporting reflects source mix.
SEARCH_COST_USD = 0.0


_BREAKER = CircuitBreaker(
    name="yelp",
    failure_threshold=5,
    cooldown_s=60.0,
    expected_exceptions=(httpx.HTTPError, httpx.HTTPStatusError),
)


class YelpClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        session: AsyncSession | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.yelp_api_key
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._session = session
        self._owns_http = http is None
        self._breaker = breaker or _BREAKER

    async def __aenter__(self) -> YelpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search_businesses(
        self,
        term: str,
        *,
        location: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        max_results: int = 20,
        job_id: UUID | None = None,
    ) -> list[YelpBusiness]:
        """Search Yelp for businesses matching `term`.

        Either `location` (free-form address/city) or (`latitude`, `longitude`)
        must be supplied — Yelp rejects searches without a location anchor.
        """
        if not self._api_key:
            raise RuntimeError("YELP_API_KEY is not set")
        if location is None and (latitude is None or longitude is None):
            raise ValueError("Yelp search requires `location` or `latitude`+`longitude`")

        params: dict[str, Any] = {
            "term": term,
            "limit": min(max(1, max_results), 50),
        }
        if location is not None:
            params["location"] = location
        else:
            params["latitude"] = latitude
            params["longitude"] = longitude

        url = f"{YELP_BASE_URL}/businesses/search"
        content_hash = _hash_request(url, params)

        cached = await self._cache_lookup(content_hash)
        if cached is not None:
            log.info("yelp_cache_hit", term=term, location=location)
            return _parse_businesses(cached)

        headers = {"Authorization": f"Bearer {self._api_key}"}

        async def _do_get() -> httpx.Response:
            response = await self._http.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response

        started = time.monotonic()
        try:
            response = await self._breaker.call(_do_get)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            duration_ms = int((time.monotonic() - started) * 1000)
            await self._record_fetch(
                url=_url_for_audit(url, params),
                method="GET",
                response_status=response.status_code,
                bytes_fetched=len(response.content),
                duration_ms=duration_ms,
                content_hash=content_hash,
                payload=_try_json(response),
                cost_usd=SEARCH_COST_USD,
                job_id=job_id,
            )
            raise
        duration_ms = int((time.monotonic() - started) * 1000)

        payload = _try_json(response)
        await self._record_fetch(
            url=_url_for_audit(url, params),
            method="GET",
            response_status=response.status_code,
            bytes_fetched=len(response.content),
            duration_ms=duration_ms,
            content_hash=content_hash,
            payload=payload,
            cost_usd=SEARCH_COST_USD,
            job_id=job_id,
        )
        return _parse_businesses(payload or {})

    async def _cache_lookup(self, content_hash: str) -> dict[str, Any] | None:
        if self._session is None:
            return None
        # The payload may already have been nulled out by the 24h retention
        # sweep — filter those out so we only reuse live, complete responses.
        cutoff = datetime.now(UTC) - CACHE_TTL
        stmt = (
            select(RawFetch)
            .where(
                RawFetch.content_hash == content_hash,
                RawFetch.source_slug == SOURCE_SLUG,
                RawFetch.response_status == 200,
                RawFetch.payload.is_not(None),
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


def _hash_request(url: str, params: dict[str, Any]) -> str:
    blob = json.dumps({"url": url, "params": params}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _url_for_audit(url: str, params: dict[str, Any]) -> str:
    if not params:
        return url
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"{url}?{query}"


def _try_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_businesses(payload: dict[str, Any]) -> list[YelpBusiness]:
    raw_list = payload.get("businesses") or []
    parsed: list[YelpBusiness] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        try:
            parsed.append(YelpBusiness.model_validate(raw))
        except Exception as exc:  # malformed entry — skip, don't fail the batch
            log.warning("yelp_parse_skipped", error=str(exc))
    return parsed
