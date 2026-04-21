"""Tests for Compliant Mode: adapter filtering + settings endpoint."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.places import Place
from app.services.discovery import PlacesAdapter, SmartRouter, YelpAdapter


class _FakePlaces:
    async def text_search(
        self,
        query: str,
        *,
        region_code: str | None,
        max_results: int,
        job_id: UUID | None,
    ) -> list[Place]:
        return []


class _FakeYelp:
    async def search_businesses(self, **kwargs: object) -> list[object]:
        return []


def test_smart_router_drops_non_tier1_in_compliant_mode() -> None:
    places = PlacesAdapter(_FakePlaces())  # type: ignore[arg-type]
    yelp = YelpAdapter(_FakeYelp())  # type: ignore[arg-type]

    permissive = SmartRouter([places, yelp], compliant_mode=False)
    assert {a.name for a in permissive.adapters} == {"google_places", "yelp"}

    strict = SmartRouter([places, yelp], compliant_mode=True)
    assert {a.name for a in strict.adapters} == {"google_places"}
    assert strict.compliant_mode is True


def test_smart_router_rejects_empty_adapter_set_in_compliant_mode() -> None:
    yelp = YelpAdapter(_FakeYelp())  # type: ignore[arg-type]
    # Yelp alone would leave zero Tier-1 adapters after filtering — that's
    # a misconfiguration we must fail loudly on.
    with pytest.raises(ValueError):
        SmartRouter([yelp], compliant_mode=True)


@pytest.mark.asyncio
async def test_settings_compliance_endpoint_returns_flags() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/settings/compliance")
    assert resp.status_code == 200
    body = resp.json()
    assert "compliant_mode" in body
    assert "jurisdiction" in body
    assert isinstance(body["compliant_mode"], bool)
