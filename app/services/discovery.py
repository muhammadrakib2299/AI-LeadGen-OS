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
from app.models.foursquare import FsqPlace
from app.models.places import Place
from app.models.yelp import YelpBusiness
from app.services.foursquare import SEARCH_COST_USD as FSQ_SEARCH_COST_USD
from app.services.foursquare import FoursquareClient
from app.services.places import TEXT_SEARCH_COST_USD, PlacesClient
from app.services.yelp import SEARCH_COST_USD as YELP_SEARCH_COST_USD
from app.services.yelp import YelpClient

log = get_logger(__name__)


@runtime_checkable
class DiscoveryAdapter(Protocol):
    """A single discovery source. Wraps the underlying client + cost model."""

    name: str
    cost_per_call_usd: float
    # True when this source is a Tier-1 official API with a clear ToS that
    # permits indefinite storage of derived facts (Google Places, companies
    # registers). False for sources with storage restrictions (Yelp = 24h)
    # or legally-ambiguous scraped data. Compliant Mode only consults
    # adapters where this is True. See sources.md.
    is_official_api: bool

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
    # Google Maps Platform ToS permits storing derived Places data for up
    # to 30 days (place_id indefinitely) — compliant for Tier-1 use.
    is_official_api = True

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
    # Yelp's 24h storage rule makes it NOT a Tier-1 source for Compliant Mode;
    # it's filtered out when the operator toggles compliance on.
    is_official_api = False

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


class FoursquareAdapter:
    """Foursquare Places v3 fallback. Maps fsq places to the common `Place` shape.

    Tier-1 official API under ToS — stays enabled in Compliant Mode. Only
    objective facts (name, address, phone, website, coords) are carried;
    `categories` and other curated signals are dropped so the entities
    table stays source-neutral.
    """

    name = "foursquare"
    cost_per_call_usd = FSQ_SEARCH_COST_USD
    is_official_api = True

    def __init__(self, client: FoursquareClient) -> None:
        self._client = client

    async def search(
        self,
        query: str,
        *,
        region_code: str | None,
        max_results: int,
        job_id: UUID | None,
    ) -> list[Place]:
        near = _location_hint_from_query(query, region_code)
        if not near:
            log.info("foursquare_adapter_skipped_no_location", query=query)
            return []
        fsq_places = await self._client.search_places(
            query=_term_from_query(query),
            near=near,
            max_results=max_results,
            job_id=job_id,
        )
        return [_fsq_to_place(p) for p in fsq_places]


def _fsq_to_place(p: FsqPlace) -> Place:
    loc = p.location
    city = loc.locality if loc else None
    country = loc.country if loc else None
    address_components: list[dict[str, object]] = []
    if city:
        address_components.append(
            {"longText": city, "shortText": city, "types": ["locality"]}
        )
    if country:
        address_components.append(
            {"longText": country, "shortText": country, "types": ["country"]}
        )
    coords: dict[str, float] | None = None
    if (
        p.geocodes
        and p.geocodes.main
        and p.geocodes.main.latitude is not None
        and p.geocodes.main.longitude is not None
    ):
        coords = {
            "latitude": p.geocodes.main.latitude,
            "longitude": p.geocodes.main.longitude,
        }
    formatted = loc.formatted_address if loc else None

    return Place.model_validate(
        {
            "id": f"fsq:{p.fsq_id}",
            "displayName": {"text": p.name, "languageCode": None},
            "formattedAddress": formatted,
            "addressComponents": address_components,
            "location": coords,
            "types": [],
            "websiteUri": p.website,
            "nationalPhoneNumber": p.tel,
            "internationalPhoneNumber": p.tel,
        }
    )


class AllAdaptersDownError(Exception):
    """Raised when every adapter either refused or errored out."""


class SmartRouter:
    def __init__(
        self,
        adapters: list[DiscoveryAdapter],
        *,
        compliant_mode: bool = False,
    ) -> None:
        if not adapters:
            raise ValueError("SmartRouter needs at least one adapter")
        # Filter to Tier-1 official APIs when compliant mode is on. We do
        # this at construction rather than per-call so the failure mode
        # ("all adapters down") is the same whether compliance filtered
        # them out or circuit breakers tripped them.
        if compliant_mode:
            adapters = [a for a in adapters if getattr(a, "is_official_api", False)]
            if not adapters:
                raise ValueError(
                    "SmartRouter in compliant mode has no Tier-1 adapters — "
                    "at minimum PlacesAdapter must be registered"
                )
        self._adapters = adapters
        self._compliant_mode = compliant_mode

    @property
    def adapters(self) -> list[DiscoveryAdapter]:
        return list(self._adapters)

    @property
    def compliant_mode(self) -> bool:
        return self._compliant_mode

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
