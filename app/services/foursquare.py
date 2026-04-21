"""Foursquare Places v3 /places/search client.

Mirrors the OpenCorporates pattern (audit logs, cache-via-raw-fetches,
circuit breaker). No special ToS retention rules — attribution is the main
constraint, which the UI handles.

API docs: https://docs.foursquare.com/developer/reference/place-search
Rate limits: generous free tier; paid plans for higher throughput.
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
from app.models.foursquare import FsqPlace

log = get_logger(__name__)

FOURSQUARE_BASE_URL = "https://api.foursquare.com/v3"
SOURCE_SLUG = "foursquare"
CACHE_TTL = timedelta(days=30)
LEGAL_BASIS = "legitimate_interest"
SEARCH_COST_USD = 0.0  # free-tier nominal

_BREAKER = CircuitBreaker(
    name="foursquare",
    failure_threshold=5,
    cooldown_s=60.0,
    expected_exceptions=(httpx.HTTPError, httpx.HTTPStatusError),
)


class FoursquareClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        session: AsyncSession | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.foursquare_api_key
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._session = session
        self._owns_http = http is None
        self._breaker = breaker or _BREAKER

    async def __aenter__(self) -> FoursquareClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search_places(
        self,
        query: str,
        *,
        near: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        max_results: int = 20,
        job_id: UUID | None = None,
    ) -> list[FsqPlace]:
        """Search Foursquare for places matching `query`.

        Either `near` (free-form city/address) or (`latitude`, `longitude`)
        must be given — Foursquare demands a location anchor.
        """
        if not self._api_key:
            raise RuntimeError("FOURSQUARE_API_KEY is not set")
        if near is None and (latitude is None or longitude is None):
            raise ValueError("Foursquare search requires `near` or `latitude`+`longitude`")

        params: dict[str, Any] = {
            "query": query,
            "limit": min(max(1, max_results), 50),
        }
        if near is not None:
            params["near"] = near
        else:
            params["ll"] = f"{latitude},{longitude}"

        url = f"{FOURSQUARE_BASE_URL}/places/search"
        content_hash = _hash_request(url, params)

        cached = await self._cache_lookup(content_hash)
        if cached is not None:
            log.info("foursquare_cache_hit", query=query, near=near)
            return _parse_places(cached)

        # v3 auth header is the raw key — no "Bearer " prefix.
        headers = {"Authorization": self._api_key, "accept": "application/json"}

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
        row = (await self._session.execute(stmt)).scalar_one_or_none()
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


def _parse_places(payload: dict[str, Any]) -> list[FsqPlace]:
    raw_list = payload.get("results") or []
    parsed: list[FsqPlace] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        try:
            parsed.append(FsqPlace.model_validate(raw))
        except Exception as exc:
            log.warning("foursquare_parse_skipped", error=str(exc))
    return parsed
