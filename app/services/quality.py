"""Quality scorer: produces a 0-100 score per entity.

Factors (max 100):
- Completeness (50 pts): which required fields are present
- Source trust (30 pts): mean per-field confidence from `field_sources`
- Freshness (20 pts): how recent the newest `fetched_at` timestamp is

Entities scoring below REVIEW_THRESHOLD are routed to the review queue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

COMPLETENESS_WEIGHTS: dict[str, int] = {
    "name": 5,
    "website": 10,
    "email": 15,
    "phone": 10,
    "address": 5,
    "city": 3,
    "country": 2,
}
# Sum = 50

FRESHNESS_BANDS: tuple[tuple[timedelta, int], ...] = (
    (timedelta(days=7), 20),
    (timedelta(days=30), 15),
    (timedelta(days=90), 10),
)
STALE_POINTS = 5

REVIEW_THRESHOLD = 70


def score_entity(
    *,
    values: dict[str, str | None],
    field_sources: dict[str, Any],
    now: datetime | None = None,
) -> int:
    completeness = _completeness_points(values)
    trust = _trust_points(field_sources)
    freshness = _freshness_points(field_sources, now=now or datetime.now(UTC))
    return max(0, min(100, completeness + trust + freshness))


def review_status_for(score: int) -> str:
    return "review" if score < REVIEW_THRESHOLD else "approved"


def _completeness_points(values: dict[str, str | None]) -> int:
    return sum(w for field, w in COMPLETENESS_WEIGHTS.items() if values.get(field))


def _trust_points(field_sources: dict[str, Any]) -> int:
    confidences: list[float] = []
    for fs in field_sources.values():
        if not isinstance(fs, dict):
            continue
        conf = fs.get("confidence")
        if isinstance(conf, int | float):
            confidences.append(max(0.0, min(1.0, float(conf))))
    if not confidences:
        return 0
    return round(30 * (sum(confidences) / len(confidences)))


def _freshness_points(field_sources: dict[str, Any], *, now: datetime) -> int:
    newest: datetime | None = None
    for fs in field_sources.values():
        if not isinstance(fs, dict):
            continue
        ts_str = fs.get("fetched_at")
        if not isinstance(ts_str, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if newest is None or ts > newest:
            newest = ts
    if newest is None:
        return 0
    age = now - newest
    for band, pts in FRESHNESS_BANDS:
        if age <= band:
            return pts
    return STALE_POINTS
