"""Tests for the CircuitBreaker state machine + one Places integration check."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.core.circuit import CircuitBreaker, CircuitOpenError
from app.services.places import PLACES_BASE_URL, PlacesClient


class _BoomError(Exception):
    pass


async def _ok() -> str:
    return "ok"


def _boom_call():
    async def _inner() -> str:
        raise _BoomError("nope")

    return _inner


async def test_closed_breaker_passes_calls_through() -> None:
    cb = CircuitBreaker("t", failure_threshold=2, cooldown_s=0.1)
    assert await cb.call(_ok) == "ok"
    assert cb.state == "closed"


async def test_breaker_opens_after_threshold() -> None:
    cb = CircuitBreaker("t", failure_threshold=2, cooldown_s=0.1)
    for _ in range(2):
        with pytest.raises(_BoomError):
            await cb.call(_boom_call())
    assert cb.state == "open"


async def test_open_breaker_short_circuits_without_invoking_func() -> None:
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_s=10.0)
    with pytest.raises(_BoomError):
        await cb.call(_boom_call())
    assert cb.state == "open"

    invoked = False

    async def _marker() -> str:
        nonlocal invoked
        invoked = True
        return "reached"

    with pytest.raises(CircuitOpenError):
        await cb.call(_marker)
    assert invoked is False


async def test_cooldown_expiry_moves_to_half_open_and_probe_success_closes() -> None:
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_s=0.05)
    with pytest.raises(_BoomError):
        await cb.call(_boom_call())
    assert cb.state == "open"

    await asyncio.sleep(0.06)
    # Probe succeeds → breaker closes.
    assert await cb.call(_ok) == "ok"
    assert cb.state == "closed"


async def test_probe_failure_reopens_breaker() -> None:
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_s=0.05)
    with pytest.raises(_BoomError):
        await cb.call(_boom_call())
    await asyncio.sleep(0.06)

    # Half-open probe fails → back to open.
    with pytest.raises(_BoomError):
        await cb.call(_boom_call())
    assert cb.state == "open"


async def test_success_clears_failure_count() -> None:
    cb = CircuitBreaker("t", failure_threshold=3, cooldown_s=1.0)
    with pytest.raises(_BoomError):
        await cb.call(_boom_call())
    assert await cb.call(_ok) == "ok"
    assert cb.state == "closed"
    # Should need a fresh 3 failures to open, not 2.
    for _ in range(2):
        with pytest.raises(_BoomError):
            await cb.call(_boom_call())
    assert cb.state == "closed"


async def test_unexpected_exception_type_is_not_counted() -> None:
    class _Unexpected(BaseException):
        pass

    cb = CircuitBreaker(
        "t",
        failure_threshold=1,
        cooldown_s=1.0,
        expected_exceptions=(_BoomError,),
    )

    async def _raise_unexpected() -> None:
        raise _Unexpected()

    with pytest.raises(_Unexpected):
        await cb.call(_raise_unexpected)
    assert cb.state == "closed"


async def test_reset_returns_to_closed() -> None:
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_s=10.0)
    with pytest.raises(_BoomError):
        await cb.call(_boom_call())
    assert cb.state == "open"
    await cb.reset()
    assert cb.state == "closed"
    assert await cb.call(_ok) == "ok"


@respx.mock
async def test_places_client_breaker_short_circuits_after_repeated_500s() -> None:
    respx.post(f"{PLACES_BASE_URL}/places:searchText").mock(
        return_value=httpx.Response(500, json={"error": "backend down"})
    )
    breaker = CircuitBreaker(
        "google_places_test",
        failure_threshold=3,
        cooldown_s=60.0,
        expected_exceptions=(httpx.HTTPError, httpx.HTTPStatusError),
    )
    async with httpx.AsyncClient() as http:
        client = PlacesClient(http=http, api_key="test-key", breaker=breaker)

        for _ in range(3):
            with pytest.raises(httpx.HTTPStatusError):
                await client.text_search("restaurants in Paris")
        assert breaker.state == "open"

        # Next call should fail fast without hitting HTTP.
        before = respx.calls.call_count
        with pytest.raises(CircuitOpenError):
            await client.text_search("restaurants in Paris")
        assert respx.calls.call_count == before
