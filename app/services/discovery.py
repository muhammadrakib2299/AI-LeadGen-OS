"""Discovery layer — adapters + SmartRouter.

Phase 3 introduces this seam so the rest of the pipeline stops talking to
Google Places directly. Adding Yelp, OpenCorporates, Foursquare etc. later
is then just a matter of dropping another `DiscoveryAdapter` into the
router's list — the JobRunner never needs to know.

Current routing strategy is deliberately simple: try adapters in priority
order, skip any whose circuit breaker is open, return the first list that
comes back (even if empty). Per overview.md §5 the longer-term spec is
"cache → free API → paid API → compliant scrape" — that lives here when
we have more than one adapter to choose between.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from app.core.circuit import CircuitOpenError
from app.core.logging import get_logger
from app.models.places import Place
from app.models.yelp import YelpBusiness
from app.services.places import TEXT_SEARCH_COST_USD, PlacesClient
from app.services.yelp import SEARCH_COST_USD as YELP_SEARCH_COST_USD
from app.services.yelp import YelpClient

log = get_logger(__name__)


@runtime_checkable
class DiscoveryAdapter(Protocol):
    """A single discovery source. Wraps the underlying client + cost model."""

    name: str
    cost_per_call_usd: float

    async def search(
        self,
        query: str,
        *,
        region_code: str | None,
        max_results: int,
        job_id: UUID | None,
    ) -> list[Place]: ...


class PlacesAdapter:
    """Adapter over the existing PlacesClient.

    Keeps PlacesClient's cache + circuit-breaker behavior intact — this is
    just a thin facade so the router has a uniform interface to call.
    """

    name = "google_places"
    cost_per_call_usd = TEXT_SEARCH_COST_USD

    def __init__(self, client: PlacesClient) -> None:
        self._client = client

    async def search(
        self,
        query: str,
        *,
        region_code: str | None,
        max_results: int,
        job_id: UUID | None,
    ) -> list[Place]:
        return await self._client.text_search(
            query,
            region_code=region_code,
            max_results=max_results,
            job_id=job_id,
        )


class YelpAdapter:
    """Yelp Fusion fallback. Maps Yelp businesses to the common `Place` shape.

    Only objective, widely-available facts (name, address, phone, coords,
    city, country) are carried over. Yelp-curated content (rating, review
    count, category taxonomy, price tier) is intentionally dropped so it
    never reaches the entities table — see app/services/yelp.py for the
    full ToS rationale. The Yelp business ID is prefixed with `yelp:` so
    downstream code can tell it apart from Google `place_id`s.
    """

    name = "yelp"
    cost_per_call_usd = YELP_SEARCH_COST_USD

    def __init__(self, client: YelpClient) -> None:
        self._client = client

    async def search(
        self,
        query: str,
        *,
        region_code: str | None,
        max_results: int,
        job_id: UUID | None,
    ) -> list[Place]:
        # Yelp requires an anchor — free-form location text works for the
        # major cities EU/UK operators target. If the router hands us a
        # bare query without a location, skip rather than error so the next
        # adapter (or caller) can try instead.
        location_hint = _location_hint_from_query(query, region_code)
        if not location_hint:
            log.info("yelp_adapter_skipped_no_location", query=query)
            return []
        businesses = await self._client.search_businesses(
            term=_term_from_query(query),
            location=location_hint,
            max_results=max_results,
            job_id=job_id,
        )
        return [_yelp_to_place(b) for b in businesses]


def _term_from_query(query: str) -> str:
    # If the caller supplied "restaurants in Paris", strip the trailing
    # "in Paris" so Yelp's `term` parameter doesn't double-specify location.
    lowered = query.lower()
    idx = lowered.rfind(" in ")
    if idx > 0:
        return query[:idx].strip()
    return query.strip()


def _location_hint_from_query(query: str, region_code: str | None) -> str | None:
    lowered = query.lower()
    idx = lowered.rfind(" in ")
    if idx > 0:
        loc = query[idx + 4 :].strip(" ,")
        if loc:
            return loc
    return region_code


def _yelp_to_place(b: YelpBusiness) -> Place:
    # Construct a Place via its alias-accepting model so validation matches
    # what Google Places' pipeline expects downstream.
    location = b.location
    city = location.city if location else None
    country = location.country if location else None
    address_components: list[dict[str, object]] = []
    if city:
        address_components.append(
            {"longText": city, "shortText": city, "types": ["locality"]}
        )
    if country:
        address_components.append(
            {
                "longText": country,
                "shortText": country,
                "types": ["country"],
            }
        )
    coords: dict[str, float] | None = None
    if b.coordinates and b.coordinates.latitude is not None and b.coordinates.longitude is not None:
        coords = {"latitude": b.coordinates.latitude, "longitude": b.coordinates.longitude}

    return Place.model_validate(
        {
            "id": f"yelp:{b.id}",
            "displayName": {"text": b.name, "languageCode": None},
            "formattedAddress": b.formatted_address,
            "addressComponents": address_components,
            "location": coords,
            "types": [],
            "nationalPhoneNumber": b.display_phone,
            "internationalPhoneNumber": b.phone,
        }
    )


class AllAdaptersDownError(Exception):
    """Raised when every adapter either refused or errored out."""


class SmartRouter:
    def __init__(self, adapters: list[DiscoveryAdapter]) -> None:
        if not adapters:
            raise ValueError("SmartRouter needs at least one adapter")
        self._adapters = adapters

    @property
    def adapters(self) -> list[DiscoveryAdapter]:
        return list(self._adapters)

    async def search(
        self,
        query: str,
        *,
        region_code: str | None,
        max_results: int,
        job_id: UUID | None,
    ) -> tuple[list[Place], float]:
        """Return (places, cost_usd). Cost is the first successful adapter's cost.

        Tries adapters in priority order, skipping ones whose circuit is open.
        Raises `AllAdaptersDownError` only when every adapter refused or errored.
        """
        errors: list[str] = []
        for adapter in self._adapters:
            try:
                results = await adapter.search(
                    query,
                    region_code=region_code,
                    max_results=max_results,
                    job_id=job_id,
                )
            except CircuitOpenError as exc:
                log.info("discovery_adapter_circuit_open", adapter=adapter.name)
                errors.append(f"{adapter.name}: circuit open ({exc})")
                continue
            except Exception as exc:
                log.warning(
                    "discovery_adapter_error",
                    adapter=adapter.name,
                    error=str(exc),
                )
                errors.append(f"{adapter.name}: {type(exc).__name__}: {exc}")
                continue
            return results, adapter.cost_per_call_usd

        raise AllAdaptersDownError(
            "all discovery adapters failed: " + "; ".join(errors)
        )
