"""Tests for the SmartRouter + adapter layer."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.circuit import CircuitOpenError
from app.models.places import Place
from app.services.discovery import AllAdaptersDownError, DiscoveryAdapter, SmartRouter


def _fake_place(place_id: str, name: str) -> Place:
    return Place.model_validate(
        {
            "id": place_id,
            "displayName": {"text": name, "languageCode": "en"},
        }
    )


class _StubAdapter:
    """Records calls and returns pre-baked results (or raises)."""

    def __init__(
        self,
        name: str,
        *,
        cost: float = 0.01,
        result: list[Place] | None = None,
        raises: Exception | None = None,
        is_official_api: bool = True,
    ) -> None:
        self.name = name
        self.cost_per_call_usd = cost
        self.is_official_api = is_official_api
        self._result = result if result is not None else []
        self._raises = raises
        self.calls = 0

    async def search(
        self,
        query: str,
        *,
        region_code: str | None,
        max_results: int,
        job_id: UUID | None,
    ) -> list[Place]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._result


def test_stub_adapter_satisfies_protocol() -> None:
    # Sanity check — the protocol is runtime_checkable.
    assert isinstance(_StubAdapter("x"), DiscoveryAdapter)


async def test_router_requires_at_least_one_adapter() -> None:
    with pytest.raises(ValueError):
        SmartRouter([])


async def test_router_returns_first_adapter_result_and_its_cost() -> None:
    primary = _StubAdapter("primary", cost=0.03, result=[_fake_place("p1", "Foo")])
    secondary = _StubAdapter("secondary", cost=0.02, result=[_fake_place("p2", "Bar")])
    router = SmartRouter([primary, secondary])

    places, cost = await router.search(
        "anything", region_code="FR", max_results=5, job_id=None
    )
    assert [p.id for p in places] == ["p1"]
    assert cost == 0.03
    # Secondary never consulted when primary succeeds.
    assert secondary.calls == 0


async def test_router_falls_through_on_circuit_open() -> None:
    primary = _StubAdapter(
        "primary",
        raises=CircuitOpenError("primary", cooldown_remaining_s=12.0),
    )
    secondary = _StubAdapter("secondary", cost=0.02, result=[_fake_place("p2", "Bar")])
    router = SmartRouter([primary, secondary])

    places, cost = await router.search("q", region_code=None, max_results=5, job_id=None)
    assert [p.id for p in places] == ["p2"]
    assert cost == 0.02
    assert primary.calls == 1
    assert secondary.calls == 1


async def test_router_falls_through_on_generic_error() -> None:
    primary = _StubAdapter("primary", raises=RuntimeError("upstream 500"))
    secondary = _StubAdapter("secondary", cost=0.02, result=[_fake_place("p2", "Bar")])
    router = SmartRouter([primary, secondary])

    places, cost = await router.search("q", region_code=None, max_results=5, job_id=None)
    assert [p.id for p in places] == ["p2"]
    assert cost == 0.02


async def test_router_raises_when_all_adapters_fail() -> None:
    primary = _StubAdapter("primary", raises=RuntimeError("a"))
    secondary = _StubAdapter(
        "secondary",
        raises=CircuitOpenError("secondary", cooldown_remaining_s=5.0),
    )
    router = SmartRouter([primary, secondary])

    with pytest.raises(AllAdaptersDownError) as excinfo:
        await router.search("q", region_code=None, max_results=5, job_id=None)
    msg = str(excinfo.value)
    assert "primary" in msg
    assert "secondary" in msg


async def test_router_preserves_adapter_order_and_exposes_list() -> None:
    a = _StubAdapter("a")
    b = _StubAdapter("b")
    router = SmartRouter([a, b])
    assert [x.name for x in router.adapters] == ["a", "b"]


async def test_router_returns_empty_result_without_trying_fallback() -> None:
    # An adapter returning [] is a *success* — don't waste a fallback on it.
    # Callers up the stack decide whether empty results warrant re-query.
    primary = _StubAdapter("primary", cost=0.03, result=[])
    secondary = _StubAdapter("secondary", cost=0.02, result=[_fake_place("p2", "B")])
    router = SmartRouter([primary, secondary])

    places, cost = await router.search("q", region_code=None, max_results=5, job_id=None)
    assert places == []
    assert cost == 0.03
    assert secondary.calls == 0
