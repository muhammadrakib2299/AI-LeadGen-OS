"""GET /status — snapshot of circuit breakers for the dashboard.

Each external dependency has its own `CircuitBreaker` singleton in
`app/services/*.py`. This endpoint gathers their live state so the UI can
show a compact "system status" indicator and explain why a given job is
running slowly (e.g. "Google Places circuit is open — using Yelp fallback").
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.circuit import CircuitBreaker
from app.services.email_verify import _DNS_BREAKER
from app.services.foursquare import _BREAKER as _FOURSQUARE_BREAKER
from app.services.llm import _ANTHROPIC_BREAKER
from app.services.opencorporates import _BREAKER as _OPENCORPORATES_BREAKER
from app.services.places import _PLACES_BREAKER
from app.services.yelp import _BREAKER as _YELP_BREAKER

router = APIRouter(prefix="/status", tags=["status"])


class CircuitSnapshot(BaseModel):
    name: str
    state: str  # "closed" | "open" | "half_open"


class SystemStatusResponse(BaseModel):
    # Overall health: "ok" if every circuit is closed, "degraded" if any is
    # half_open, "impaired" if any is open. Lets the UI color a single dot
    # without inspecting every row.
    overall: str
    circuits: list[CircuitSnapshot]


_BREAKERS: list[CircuitBreaker] = [
    _PLACES_BREAKER,
    _FOURSQUARE_BREAKER,
    _YELP_BREAKER,
    _OPENCORPORATES_BREAKER,
    _ANTHROPIC_BREAKER,
    _DNS_BREAKER,
]


def _overall_from(states: list[str]) -> str:
    if any(s == "open" for s in states):
        return "impaired"
    if any(s == "half_open" for s in states):
        return "degraded"
    return "ok"


@router.get("", response_model=SystemStatusResponse)
async def get_status() -> SystemStatusResponse:
    snapshots = [CircuitSnapshot(name=b.name, state=b.state) for b in _BREAKERS]
    return SystemStatusResponse(
        overall=_overall_from([s.state for s in snapshots]),
        circuits=snapshots,
    )
