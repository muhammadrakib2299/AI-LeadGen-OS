"""Tests for URL liveness — crawl-derived and direct probe."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.crawler import CrawlResult
from app.services.url_liveness import check_url_liveness, liveness_from_crawl


def _page(url: str, status: int) -> CrawlResult:
    return CrawlResult(
        url=url,
        status=status,
        content_type="text/html",
        html="<html/>" if status < 400 else None,
        duration_ms=10,
        content_hash=None,
    )


def test_liveness_from_crawl_no_pages_is_unreachable() -> None:
    result = liveness_from_crawl("https://example.com", [])
    assert result.status == "unreachable"
    assert result.confidence_boost < 1.0


def test_liveness_from_crawl_homepage_200_is_alive() -> None:
    pages = [
        _page("https://example.com/", 200),
        _page("https://example.com/contact", 404),
    ]
    result = liveness_from_crawl("https://example.com", pages)
    assert result.status == "alive"
    assert result.http_status == 200
    assert result.confidence_boost > 1.0


def test_liveness_from_crawl_all_4xx_is_dead() -> None:
    pages = [
        _page("https://example.com/", 404),
        _page("https://example.com/about", 404),
    ]
    result = liveness_from_crawl("https://example.com", pages)
    assert result.status == "dead"
    assert result.http_status == 404
    assert result.confidence_boost < 1.0


def test_liveness_from_crawl_falls_back_to_best_when_no_homepage() -> None:
    pages = [
        _page("https://example.com/contact", 200),
        _page("https://example.com/about", 500),
    ]
    result = liveness_from_crawl("https://example.com", pages)
    assert result.status == "alive"


def test_liveness_from_crawl_zero_status_is_unreachable() -> None:
    pages = [_page("https://example.com/", 0)]
    result = liveness_from_crawl("https://example.com", pages)
    assert result.status == "unreachable"


@pytest.mark.asyncio
@respx.mock
async def test_check_url_liveness_head_200() -> None:
    respx.head("https://example.com").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        result = await check_url_liveness(http, "https://example.com")
    assert result.status == "alive"
    assert result.http_status == 200


@pytest.mark.asyncio
@respx.mock
async def test_check_url_liveness_head_405_falls_back_to_get() -> None:
    respx.head("https://example.com").mock(return_value=httpx.Response(405))
    respx.get("https://example.com").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        result = await check_url_liveness(http, "https://example.com")
    assert result.status == "alive"


@pytest.mark.asyncio
@respx.mock
async def test_check_url_liveness_network_error_is_unreachable() -> None:
    respx.head("https://example.com").mock(side_effect=httpx.ConnectError("dns fail"))
    async with httpx.AsyncClient() as http:
        result = await check_url_liveness(http, "https://example.com")
    assert result.status == "unreachable"
    assert "dns fail" in (result.reason or "")
