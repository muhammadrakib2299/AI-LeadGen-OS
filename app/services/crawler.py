"""Compliant website crawler for Phase 1 lead enrichment.

Responsibilities:
- Fetch homepage + typical contact/about pages per `sources.md` Tier 4 rules.
- Respect robots.txt for the identified User-Agent.
- Throttle per-domain requests to avoid burdening small sites.
- Retry transient network errors with exponential backoff.
- When a SQLAlchemy session is attached, write a `raw_fetches` audit row
  per request (`compliance.md` §7). HTML payload is NOT persisted
  (it's large and has 90d retention pressure) — the content hash is
  stored so we can later re-fetch if needed.
- Content-hash dedupe across paths for the same site: many small-business
  sites return an identical homepage for `/` and `/contact-us`.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import RawFetch

log = get_logger(__name__)


SOURCE_SLUG = "website_crawler"
LEGAL_BASIS = "legitimate_interest"
CRAWL_COST_USD = 0.0  # self-hosted HTTP; infra cost tracked separately

# Languages covered: en, de, fr, es, pt, it. Add more as markets open up.
DEFAULT_PATHS: tuple[str, ...] = (
    "/",
    "/contact",
    "/contact-us",
    "/kontakt",
    "/contacto",
    "/contatti",
    "/nous-contacter",
    "/about",
    "/about-us",
    "/impressum",  # DE law requires site-owner contact details here
    "/mentions-legales",  # FR equivalent
)


@dataclass
class CrawlResult:
    url: str
    status: int
    content_type: str | None
    html: str | None
    duration_ms: int
    content_hash: str | None


class PerDomainLimiter:
    """Single-process per-domain gate; per-worker is fine for Phase 1."""

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval = min_interval_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def acquire(self, domain: str) -> None:
        if self._min_interval <= 0:
            return
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            last = self._last.get(domain)
            if last is not None:
                elapsed = time.monotonic() - last
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
            self._last[domain] = time.monotonic()


class Crawler:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        user_agent: str | None = None,
        session: AsyncSession | None = None,
        per_domain_interval_s: float | None = None,
    ) -> None:
        settings = get_settings()
        self._http = http or httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
        )
        self._user_agent = user_agent or settings.default_user_agent
        self._session = session
        self._owns_http = http is None
        self._robots_cache: dict[str, RobotFileParser | None] = {}
        self._limiter = PerDomainLimiter(
            per_domain_interval_s
            if per_domain_interval_s is not None
            else settings.per_domain_min_interval_seconds
        )

    async def __aenter__(self) -> Crawler:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def crawl_entity_site(
        self,
        website_url: str,
        *,
        job_id: UUID | None = None,
        paths: tuple[str, ...] = DEFAULT_PATHS,
    ) -> list[CrawlResult]:
        parsed = urlparse(website_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            log.warning("crawler_invalid_url", url=website_url)
            return []

        base = f"{parsed.scheme}://{parsed.netloc}"
        robots = await self._load_robots(base)

        results: list[CrawlResult] = []
        seen_hashes: set[str] = set()
        for path in paths:
            target = urljoin(base, path)
            if robots is not None and not robots.can_fetch(self._user_agent, target):
                log.info("crawler_disallowed_by_robots", url=target)
                continue
            res = await self._fetch_one(target, job_id=job_id)
            if res is None:
                continue
            if res.content_hash and res.content_hash in seen_hashes:
                continue
            if res.content_hash:
                seen_hashes.add(res.content_hash)
            results.append(res)
        return results

    async def _load_robots(self, base: str) -> RobotFileParser | None:
        if base in self._robots_cache:
            return self._robots_cache[base]
        robots_url = f"{base}/robots.txt"
        try:
            response = await self._http.get(
                robots_url,
                headers={"User-Agent": self._user_agent},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            log.info("crawler_robots_unreachable", url=robots_url, error=str(exc))
            self._robots_cache[base] = None
            return None
        if response.status_code >= 400:
            # RFC 9309: missing or 4xx robots.txt is interpreted as full allow.
            self._robots_cache[base] = None
            return None
        rp = RobotFileParser()
        rp.parse(response.text.splitlines())
        self._robots_cache[base] = rp
        return rp

    async def _fetch_one(self, url: str, *, job_id: UUID | None) -> CrawlResult | None:
        parsed = urlparse(url)
        await self._limiter.acquire(parsed.netloc)

        started = time.monotonic()
        try:
            response = await _retrying_get(self._http, url, self._user_agent)
        except (httpx.HTTPError, RetryError) as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            log.warning("crawler_request_failed", url=url, error=str(exc))
            await self._record_fetch(
                url=url,
                response_status=0,
                bytes_fetched=0,
                duration_ms=duration_ms,
                content_hash=None,
                payload={"error": str(exc)},
                job_id=job_id,
            )
            return None
        duration_ms = int((time.monotonic() - started) * 1000)

        content_type = response.headers.get("content-type", "")
        is_html = "text/html" in content_type or "application/xhtml" in content_type
        html = response.text if is_html else None

        content_hash = hashlib.sha256(response.content).hexdigest() if response.content else None

        await self._record_fetch(
            url=str(response.url),
            response_status=response.status_code,
            bytes_fetched=len(response.content),
            duration_ms=duration_ms,
            content_hash=content_hash,
            payload=None,
            job_id=job_id,
        )

        return CrawlResult(
            url=str(response.url),
            status=response.status_code,
            content_type=content_type or None,
            html=html if response.status_code < 400 else None,
            duration_ms=duration_ms,
            content_hash=content_hash,
        )

    async def _record_fetch(
        self,
        *,
        url: str,
        response_status: int,
        bytes_fetched: int,
        duration_ms: int,
        content_hash: str | None,
        payload: dict[str, Any] | None,
        job_id: UUID | None,
    ) -> None:
        if self._session is None:
            return
        self._session.add(
            RawFetch(
                job_id=job_id,
                source_slug=SOURCE_SLUG,
                url=url,
                method="GET",
                legal_basis=LEGAL_BASIS,
                response_status=response_status,
                bytes_fetched=bytes_fetched,
                duration_ms=duration_ms,
                content_hash=content_hash,
                payload=payload,
                cost_usd=CRAWL_COST_USD,
            )
        )
        await self._session.flush()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
    retry=retry_if_exception_type((httpx.TransportError, httpx.ReadTimeout)),
    reraise=True,
)
async def _retrying_get(http: httpx.AsyncClient, url: str, user_agent: str) -> httpx.Response:
    return await http.get(url, headers={"User-Agent": user_agent})
