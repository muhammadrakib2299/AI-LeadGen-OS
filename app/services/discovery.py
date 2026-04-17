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
from app.services.places import TEXT_SEARCH_COST_USD, PlacesClient

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
