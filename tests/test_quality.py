"""Unit tests for the quality scorer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.quality import (
    REVIEW_THRESHOLD,
    review_status_for,
    score_entity,
)


def _fresh_sources(now: datetime, confidence: float = 0.9) -> dict[str, dict]:
    ts = now.isoformat()
    return {
        "name": {"source": "google_places", "fetched_at": ts, "confidence": confidence},
        "website": {"source": "google_places", "fetched_at": ts, "confidence": confidence},
        "email": {"source": "crawler", "fetched_at": ts, "confidence": confidence},
        "phone": {"source": "crawler", "fetched_at": ts, "confidence": confidence},
        "address": {"source": "google_places", "fetched_at": ts, "confidence": confidence},
    }


def test_full_data_scores_near_100() -> None:
    now = datetime.now(UTC)
    values = {
        "name": "X",
        "website": "https://x.example.com",
        "email": "hi@x.example.com",
        "phone": "+33142000000",
        "address": "1 Main St",
        "city": "Paris",
        "country": "FR",
    }
    score = score_entity(values=values, field_sources=_fresh_sources(now, 1.0), now=now)
    assert score == 100


def test_empty_data_scores_zero() -> None:
    score = score_entity(
        values={
            k: None for k in ("name", "website", "email", "phone", "address", "city", "country")
        },
        field_sources={},
        now=datetime.now(UTC),
    )
    assert score == 0


def test_missing_email_drops_below_threshold_for_sparse_data() -> None:
    now = datetime.now(UTC)
    values = {
        "name": "X",
        "website": None,
        "email": None,
        "phone": None,
        "address": None,
        "city": "Paris",
        "country": "FR",
    }
    score = score_entity(values=values, field_sources=_fresh_sources(now, 0.9), now=now)
    assert score < REVIEW_THRESHOLD
    assert review_status_for(score) == "review"


def test_stale_data_gets_only_stale_freshness_points() -> None:
    now = datetime.now(UTC)
    old_ts = (now - timedelta(days=200)).isoformat()
    sources = {
        "name": {"source": "x", "fetched_at": old_ts, "confidence": 1.0},
        "email": {"source": "x", "fetched_at": old_ts, "confidence": 1.0},
    }
    values = {
        "name": "X",
        "email": "h@x.fr",
        "website": None,
        "phone": None,
        "address": None,
        "city": None,
        "country": None,
    }
    fresh_values_same = score_entity(
        values=values,
        field_sources={k: {**v, "fetched_at": now.isoformat()} for k, v in sources.items()},
        now=now,
    )
    stale = score_entity(values=values, field_sources=sources, now=now)
    assert stale < fresh_values_same


def test_bad_timestamp_ignored_without_crashing() -> None:
    sources = {"name": {"source": "x", "fetched_at": "not-a-date", "confidence": 1.0}}
    score = score_entity(
        values={"name": "X"}
        | dict.fromkeys(("website", "email", "phone", "address", "city", "country"), None),
        field_sources=sources,
        now=datetime.now(UTC),
    )
    # Should not raise; freshness contribution = 0 but completeness + trust still count.
    assert score > 0


@pytest.mark.parametrize(
    "score,expected",
    [(0, "review"), (50, "review"), (69, "review"), (70, "approved"), (99, "approved")],
)
def test_review_status_for(score: int, expected: str) -> None:
    assert review_status_for(score) == expected
