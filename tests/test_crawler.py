"""Tests for Crawler — respx-mocked HTTP, no real network."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.crawler import Crawler, PerDomainLimiter, parse_retry_after

SITE_BASE = "https://example.co.uk"


def _text_html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


@respx.mock
async def test_crawler_fetches_defined_paths_and_returns_html() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(return_value=_text_html("<h1>home</h1>"))
    respx.get(f"{SITE_BASE}/contact").mock(return_value=_text_html("<h1>contact</h1>"))
    # All other DEFAULT_PATHS return 404
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(
        return_value=httpx.Response(404, text="", headers={"content-type": "text/html"})
    )

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        results = await crawler.crawl_entity_site(SITE_BASE)

    assert any(r.html == "<h1>home</h1>" for r in results)
    assert any(r.html == "<h1>contact</h1>" for r in results)
    assert all(r.url.startswith(SITE_BASE) for r in results)


@respx.mock
async def test_crawler_sends_configured_user_agent() -> None:
    route = respx.get(f"{SITE_BASE}/").mock(return_value=_text_html("ok"))
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, user_agent="Custom/1.0", per_domain_interval_s=0)
        await crawler.crawl_entity_site(SITE_BASE)

    assert route.called
    assert route.calls.last.request.headers["User-Agent"] == "Custom/1.0"


@respx.mock
async def test_crawler_respects_robots_disallow() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(
        return_value=httpx.Response(
            200,
            text="User-agent: *\nDisallow: /contact\n",
            headers={"content-type": "text/plain"},
        )
    )
    home = respx.get(f"{SITE_BASE}/").mock(return_value=_text_html("home"))
    contact = respx.get(f"{SITE_BASE}/contact").mock(return_value=_text_html("should not hit"))
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        await crawler.crawl_entity_site(SITE_BASE)

    assert home.called
    assert not contact.called


@respx.mock
async def test_crawler_dedupes_identical_pages_by_content_hash() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    same_html = "<html><body>identical</body></html>"
    respx.get(f"{SITE_BASE}/").mock(return_value=_text_html(same_html))
    respx.get(f"{SITE_BASE}/contact").mock(return_value=_text_html(same_html))
    respx.get(f"{SITE_BASE}/contact-us").mock(return_value=_text_html(same_html))
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        results = await crawler.crawl_entity_site(SITE_BASE)

    html_results = [r for r in results if r.html == same_html]
    assert len(html_results) == 1


@respx.mock
async def test_crawler_invalid_url_returns_empty_list() -> None:
    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        assert await crawler.crawl_entity_site("not-a-url") == []
        assert await crawler.crawl_entity_site("ftp://example.com") == []


async def test_per_domain_limiter_enforces_interval() -> None:
    import time as _time

    limiter = PerDomainLimiter(min_interval_s=0.05)
    t0 = _time.monotonic()
    await limiter.acquire("example.com")
    await limiter.acquire("example.com")
    elapsed = _time.monotonic() - t0
    assert elapsed >= 0.04  # allow a little slack


@respx.mock
async def test_crawler_handles_network_error_without_raising() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    # All path requests raise a transport error
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(side_effect=httpx.ConnectError("boom"))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        results = await crawler.crawl_entity_site(SITE_BASE)

    # All failed fetches return None and are filtered out.
    assert results == []


@respx.mock
async def test_crawler_binary_content_returns_no_html() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(
        return_value=httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\n",
            headers={"content-type": "image/png"},
        )
    )
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        results = await crawler.crawl_entity_site(SITE_BASE)

    assert any(r.html is None and r.status == 200 for r in results)


@pytest.mark.asyncio
@respx.mock
async def test_crawler_writes_audit_rows(db_session) -> None:
    from sqlalchemy import select

    from app.db.models import RawFetch
    from app.services.crawler import SOURCE_SLUG

    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(return_value=_text_html("home"))
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(
        return_value=httpx.Response(404, text="missing", headers={"content-type": "text/html"})
    )

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, session=db_session, per_domain_interval_s=0)
        await crawler.crawl_entity_site(SITE_BASE)

    rows = (
        (await db_session.execute(select(RawFetch).where(RawFetch.source_slug == SOURCE_SLUG)))
        .scalars()
        .all()
    )
    # One row per path attempted (excluding skipped due to robots).
    assert len(rows) >= 1
    assert any(r.response_status == 200 for r in rows)
    assert all(r.method == "GET" for r in rows)
    assert all(r.legal_basis == "legitimate_interest" for r in rows)


def test_parse_retry_after_delta_seconds() -> None:
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after("  12.5 ") == 12.5
    assert parse_retry_after("0") == 0.0


def test_parse_retry_after_http_date() -> None:
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    future = datetime.now(UTC) + timedelta(seconds=30)
    header = format_datetime(future, usegmt=True)
    result = parse_retry_after(header)
    assert result is not None
    # Tolerate a few seconds of drift due to clock + parse latency.
    assert 25 <= result <= 35


def test_parse_retry_after_rejects_garbage() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("not-a-date") is None


async def test_limiter_cooldown_blocks_until_it_expires() -> None:
    import time as _time

    limiter = PerDomainLimiter(min_interval_s=0)
    limiter.set_cooldown("example.com", 0.08)
    t0 = _time.monotonic()
    await limiter.acquire("example.com")
    elapsed = _time.monotonic() - t0
    assert elapsed >= 0.06  # waited roughly the cooldown


async def test_limiter_cooldown_is_capped() -> None:
    from app.services.crawler import RETRY_AFTER_MAX_S

    limiter = PerDomainLimiter(min_interval_s=0)
    limiter.set_cooldown("example.com", 100_000.0)
    # cooldown_remaining should never exceed the cap.
    assert limiter.cooldown_remaining("example.com") <= RETRY_AFTER_MAX_S + 0.5


@respx.mock
async def test_crawler_honors_retry_after_on_429() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(
        return_value=httpx.Response(
            429,
            text="rate limited",
            headers={"Retry-After": "42", "content-type": "text/plain"},
        )
    )
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        await crawler.crawl_entity_site(SITE_BASE)
        # The domain is now parked.
        remaining = crawler._limiter.cooldown_remaining("example.co.uk")
    assert 40 <= remaining <= 43


@respx.mock
async def test_crawler_honors_retry_after_on_503() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(
        return_value=httpx.Response(
            503,
            text="overloaded",
            headers={"Retry-After": "15", "content-type": "text/plain"},
        )
    )
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        await crawler.crawl_entity_site(SITE_BASE)
        remaining = crawler._limiter.cooldown_remaining("example.co.uk")
    assert 13 <= remaining <= 16


@respx.mock
async def test_crawler_ignores_retry_after_on_2xx() -> None:
    respx.get(f"{SITE_BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE_BASE}/").mock(
        return_value=httpx.Response(
            200,
            text="<h1>ok</h1>",
            headers={"Retry-After": "999", "content-type": "text/html"},
        )
    )
    respx.get(url__regex=rf"^{SITE_BASE}/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as http:
        crawler = Crawler(http=http, per_domain_interval_s=0)
        await crawler.crawl_entity_site(SITE_BASE)
        remaining = crawler._limiter.cooldown_remaining("example.co.uk")
    assert remaining == 0.0
