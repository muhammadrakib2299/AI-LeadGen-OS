"""OpenCorporates /companies/search client.

Mirrors the `PlacesClient` pattern:
- Every call writes an audit row to `raw_fetches` (compliance.md §7).
- The client consults `raw_fetches` for a <30d fresh cached response with the
  same content hash before spending a new API call.
- Circuit breaker per dependency.

OpenCorporates is a B2B enrichment source: given a company name and (optionally)
a jurisdiction code, it returns the matching legal-entity record with company
number, registered address, incorporation date, and status. Useful for EU
leads where clients care about verifiable corporate data.

API docs: https://api.opencorporates.com/documentation/API-Reference
- Free/anonymous tier: ~500 req/mo, lower RPS.
- Keyed tier: higher quotas, passed via `?api_token=...`.
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
from app.models.opencorporates import CompanyRecord

log = get_logger(__name__)

OPENCORPORATES_BASE_URL = "https://api.opencorporates.com/v0.4"
SOURCE_SLUG = "opencorporates"
CACHE_TTL = timedelta(days=30)
LEGAL_BASIS = "legitimate_interest"

# OpenCorporates' own pricing doesn't meter per-call for the free tier, but we
# log a nominal cost so per-job spend reporting stays accurate if the operator
# is on the paid tier (≈$0.001/lookup on the entry paid plan).
SEARCH_COST_USD = 0.001

_BREAKER = CircuitBreaker(
    name="opencorporates",
    failure_threshold=5,
    cooldown_s=60.0,
    expected_exceptions=(httpx.HTTPError, httpx.HTTPStatusError),
)


class OpenCorporatesClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        session: AsyncSession | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = (
            api_key if api_key is not None else settings.opencorporates_api_key
        )
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._session = session
        self._owns_http = http is None
        self._breaker = breaker or _BREAKER

    async def __aenter__(self) -> OpenCorporatesClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search_companies(
        self,
        name: str,
        *,
        jurisdiction_code: str | None = None,
        max_results: int = 5,
        job_id: UUID | None = None,
    ) -> list[CompanyRecord]:
        """Return candidate company records ordered by OpenCorporates' own relevance.

        The caller is responsible for picking which hit to trust (e.g. name +
        country match). Returning the top N rather than a single "best match"
        leaves that policy decision out of the HTTP client.
        """
        params: dict[str, Any] = {
            "q": name,
            "per_page": max_results,
            "format": "json",
        }
        if jurisdiction_code:
            params["jurisdiction_code"] = jurisdiction_code.lower()
        if self._api_key:
            params["api_token"] = self._api_key

        url = f"{OPENCORPORATES_BASE_URL}/companies/search"
        content_hash = _hash_request(url, params)

        cached = await self._cache_lookup(content_hash)
        if cached is not None:
            log.info("opencorporates_cache_hit", name=name, jurisdiction=jurisdiction_code)
            return _parse_companies(cached)

        async def _do_get() -> httpx.Response:
            response = await self._http.get(url, params=params)
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
        return _parse_companies(payload or {})

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


def _hash_request(url: str, params: dict[str, Any]) -> str:
    # Exclude api_token from the hash so keyed and anonymous calls for the same
    # query share a cache entry. The token is a credential, not part of the query.
    hashable = {k: v for k, v in params.items() if k != "api_token"}
    blob = json.dumps({"url": url, "params": hashable}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _url_for_audit(url: str, params: dict[str, Any]) -> str:
    # Drop api_token from the audit URL so secrets never land in `raw_fetches`.
    safe = {k: v for k, v in params.items() if k != "api_token"}
    if not safe:
        return url
    query = "&".join(f"{k}={v}" for k, v in sorted(safe.items()))
    return f"{url}?{query}"


def _try_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_companies(payload: dict[str, Any]) -> list[CompanyRecord]:
    # Shape: {"results": {"companies": [{"company": {...}}, ...]}}
    results = payload.get("results") or {}
    companies = results.get("companies") or []
    parsed: list[CompanyRecord] = []
    for wrapper in companies:
        raw = wrapper.get("company") if isinstance(wrapper, dict) else None
        if not isinstance(raw, dict):
            continue
        oc_id = _opencorporates_id(raw)
        name = raw.get("name")
        if not oc_id or not name:
            continue
        parsed.append(
            CompanyRecord(
                opencorporates_id=oc_id,
                name=name,
                company_number=raw.get("company_number"),
                jurisdiction_code=raw.get("jurisdiction_code"),
                registered_address=raw.get("registered_address_in_full"),
                incorporation_date=raw.get("incorporation_date"),
                company_type=raw.get("company_type"),
                current_status=raw.get("current_status"),
                opencorporates_url=raw.get("opencorporates_url"),
            )
        )
    return parsed


def _opencorporates_id(raw: dict[str, Any]) -> str | None:
    """OpenCorporates' canonical ID is '{jurisdiction}/{company_number}'."""
    jurisdiction = raw.get("jurisdiction_code")
    number = raw.get("company_number")
    if not jurisdiction or not number:
        return None
    return f"{jurisdiction}/{number}"
