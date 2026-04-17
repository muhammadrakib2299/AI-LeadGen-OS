"""URL liveness check for discovered websites.

Two entry points:

- `liveness_from_crawl(url, pages)` — cheap: reuses crawl results we already
  have from `Crawler.crawl_entity_site`. No extra HTTP. Preferred path
  inside the Phase 1/2 pipeline.
- `check_url_liveness(http, url)` — explicit HEAD (falls back to GET for
  servers that reject HEAD). For callers that haven't crawled — e.g. when
  re-verifying aged records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

from app.services.crawler import CrawlResult

LivenessStatus = Literal["alive", "dead", "unreachable", "unknown"]


@dataclass(slots=True)
class UrlLiveness:
    url: str
    status: LivenessStatus
    http_status: int | None = None
    final_url: str | None = None
    reason: str | None = None

    @property
    def confidence_boost(self) -> float:
        """Multiplier applied to the website field's confidence."""
        return {
            "alive": 1.02,
            "dead": 0.3,
            "unreachable": 0.6,
            "unknown": 1.0,
        }[self.status]


def liveness_from_crawl(url: str, pages: list[CrawlResult]) -> UrlLiveness:
    """Derive liveness from the crawler's output for the same site."""
    if not pages:
        return UrlLiveness(url=url, status="unreachable", reason="no_pages_fetched")

    homepage_host = _host_of(url)
    # Prefer the homepage result if we can find it; otherwise best status seen.
    homepage = next(
        (
            p
            for p in pages
            if _host_of(p.url) == homepage_host and urlparse(p.url).path in ("", "/")
        ),
        None,
    )
    if homepage is not None:
        return _classify(url, homepage.status, homepage.url)

    best = min(pages, key=lambda p: (p.status == 0, p.status))
    return _classify(url, best.status, best.url)


async def check_url_liveness(
    http: httpx.AsyncClient,
    url: str,
    *,
    timeout_s: float = 10.0,
) -> UrlLiveness:
    """Issue a HEAD (with GET fallback) to probe the URL directly."""
    try:
        response = await http.head(url, timeout=timeout_s, follow_redirects=True)
        if response.status_code == 405 or response.status_code >= 500:
            response = await http.get(url, timeout=timeout_s, follow_redirects=True)
    except httpx.HTTPError as exc:
        return UrlLiveness(url=url, status="unreachable", reason=str(exc))

    return _classify(url, response.status_code, str(response.url))


def _classify(url: str, status: int, final_url: str | None) -> UrlLiveness:
    if status == 0:
        return UrlLiveness(url=url, status="unreachable", http_status=status, final_url=final_url)
    if 200 <= status < 400:
        return UrlLiveness(url=url, status="alive", http_status=status, final_url=final_url)
    return UrlLiveness(url=url, status="dead", http_status=status, final_url=final_url)


def _host_of(url: str) -> str | None:
    try:
        return urlparse(url).netloc.lower() or None
    except ValueError:
        return None
