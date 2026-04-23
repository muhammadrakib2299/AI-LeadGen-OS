"""Tests for AI Ask Mode (NL → filter spec → SQL).

Pure unit tests on `coerce_spec` (no DB), plus integration tests that seed
a tenant + job + entities and run `execute_spec` end-to-end. The DB tests
skip when Postgres isn't reachable (db_session fixture handles that).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, Job, Tenant
from app.services.ask import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    AskSpec,
    ask,
    coerce_spec,
    execute_spec,
)
from app.services.llm import LLMClient


class FakeLLMClient:
    """Returns a pre-baked JSON response. Implements LLMClient protocol."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str = "",
        max_tokens: int = 0,
        tier: str = "fast",
    ) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.response


# ── coerce_spec ──────────────────────────────────────────────────────


def test_coerce_spec_drops_unknown_lead_status() -> None:
    spec = coerce_spec({"filter": {"lead_status": "warm"}, "summary": "x"})
    assert spec.filter.lead_status is None


def test_coerce_spec_normalizes_uk_to_gb() -> None:
    spec = coerce_spec({"filter": {"country": "uk"}, "summary": "x"})
    assert spec.filter.country == "GB"


def test_coerce_spec_rejects_bad_country_codes() -> None:
    spec = coerce_spec({"filter": {"country": "France"}, "summary": "x"})
    assert spec.filter.country is None


def test_coerce_spec_clamps_quality_score_range() -> None:
    over = coerce_spec({"filter": {"min_quality_score": 200}, "summary": "x"})
    assert over.filter.min_quality_score is None
    ok = coerce_spec({"filter": {"min_quality_score": 70}, "summary": "x"})
    assert ok.filter.min_quality_score == 70


def test_coerce_spec_has_email_must_be_bool() -> None:
    spec = coerce_spec({"filter": {"has_email": "yes"}, "summary": "x"})
    assert spec.filter.has_email is None
    spec = coerce_spec({"filter": {"has_email": True}, "summary": "x"})
    assert spec.filter.has_email is True


def test_coerce_spec_defaults_when_missing() -> None:
    spec = coerce_spec({})
    assert spec.sort_by == "quality_score"
    assert spec.limit == DEFAULT_LIMIT
    assert spec.summary == ""
    assert spec.filter.city is None


def test_coerce_spec_caps_limit() -> None:
    spec = coerce_spec({"limit": 5000, "summary": "x"})
    assert spec.limit == MAX_LIMIT
    spec = coerce_spec({"limit": 0, "summary": "x"})
    assert spec.limit == 1


def test_coerce_spec_falls_back_on_unknown_sort() -> None:
    spec = coerce_spec({"sort_by": "name", "summary": "x"})
    assert spec.sort_by == "quality_score"


def test_coerce_spec_strips_long_summary() -> None:
    spec = coerce_spec({"summary": "x" * 1000})
    assert len(spec.summary) == 500


def test_coerce_spec_handles_non_dict_filter() -> None:
    spec = coerce_spec({"filter": "not a dict", "summary": "x"})
    assert spec.filter.city is None


# ── ask() integration: drives the LLM seam, no DB ──────────────────


async def test_ask_passes_question_to_llm_and_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeLLMClient(
        {
            "filter": {"city": "Paris", "country": "FR"},
            "sort_by": "quality_score",
            "limit": 10,
            "summary": "restaurants in Paris",
        }
    )
    assert isinstance(fake_llm, LLMClient)

    captured: dict[str, Any] = {}

    async def fake_execute(
        db: AsyncSession, tenant_id: Any, spec: AskSpec
    ) -> list[Entity]:
        captured["spec"] = spec
        captured["tenant_id"] = tenant_id
        return []

    monkeypatch.setattr("app.services.ask.execute_spec", fake_execute)

    tenant_id = uuid4()
    result = await ask(fake_llm, db=None, tenant_id=tenant_id, question="restos in Paris")  # type: ignore[arg-type]
    assert result.spec.summary == "restaurants in Paris"
    assert result.spec.filter.city == "Paris"
    assert result.spec.filter.country == "FR"
    assert captured["tenant_id"] == tenant_id
    assert fake_llm.calls[0][1] == "restos in Paris"


# ── execute_spec: real DB, tenant-scoped ─────────────────────────────


async def _seed_tenant_with_entities(
    session: AsyncSession,
) -> tuple[Tenant, Job]:
    tenant = Tenant(name="t-ask-test")
    session.add(tenant)
    await session.flush()
    job = Job(
        tenant_id=tenant.id,
        query_raw="seed",
        limit=100,
        budget_cap_usd=5.0,
        status="succeeded",
    )
    session.add(job)
    await session.flush()

    rows = [
        Entity(
            job_id=job.id,
            name="Cafe Paris High",
            city="Paris",
            country="FR",
            category="cafe",
            email="hi@cafeparis.example",
            quality_score=92,
            review_status="approved",
            lead_status="new",
            field_sources={},
            external_ids={},
        ),
        Entity(
            job_id=job.id,
            name="Cafe Paris Low",
            city="Paris",
            country="FR",
            category="cafe",
            email=None,
            quality_score=40,
            review_status="approved",
            lead_status="new",
            field_sources={},
            external_ids={},
        ),
        Entity(
            job_id=job.id,
            name="Berlin Cafe",
            city="Berlin",
            country="DE",
            category="cafe",
            email="hi@berlin.example",
            quality_score=80,
            review_status="approved",
            lead_status="contacted",
            field_sources={},
            external_ids={},
        ),
        Entity(
            job_id=job.id,
            name="Rejected Paris",
            city="Paris",
            country="FR",
            category="cafe",
            email="x@x.example",
            quality_score=88,
            review_status="rejected",
            lead_status="new",
            field_sources={},
            external_ids={},
        ),
    ]
    session.add_all(rows)
    await session.flush()
    return tenant, job


async def test_execute_spec_filters_by_country_and_min_quality(
    db_session: AsyncSession,
) -> None:
    tenant, _ = await _seed_tenant_with_entities(db_session)
    spec = coerce_spec(
        {
            "filter": {"country": "FR", "min_quality_score": 70},
            "sort_by": "quality_score",
            "limit": 10,
            "summary": "good FR leads",
        }
    )
    rows = await execute_spec(db_session, tenant.id, spec)
    names = [r.name for r in rows]
    # Only Paris High (Low filtered by quality, Rejected by review_status,
    # Berlin by country).
    assert names == ["Cafe Paris High"]


async def test_execute_spec_has_email_true_excludes_null_emails(
    db_session: AsyncSession,
) -> None:
    tenant, _ = await _seed_tenant_with_entities(db_session)
    spec = coerce_spec(
        {"filter": {"has_email": True}, "sort_by": "quality_score", "summary": ""}
    )
    rows = await execute_spec(db_session, tenant.id, spec)
    assert all(r.email for r in rows)
    assert {r.name for r in rows} == {"Cafe Paris High", "Berlin Cafe"}


async def test_execute_spec_lead_status_filter(db_session: AsyncSession) -> None:
    tenant, _ = await _seed_tenant_with_entities(db_session)
    spec = coerce_spec(
        {"filter": {"lead_status": "contacted"}, "summary": ""}
    )
    rows = await execute_spec(db_session, tenant.id, spec)
    assert {r.name for r in rows} == {"Berlin Cafe"}


async def test_execute_spec_is_tenant_scoped(db_session: AsyncSession) -> None:
    tenant, _ = await _seed_tenant_with_entities(db_session)
    other = Tenant(name="other-tenant")
    db_session.add(other)
    await db_session.flush()

    spec = coerce_spec({"filter": {}, "summary": ""})
    rows_other = await execute_spec(db_session, other.id, spec)
    assert rows_other == []
    rows_mine = await execute_spec(db_session, tenant.id, spec)
    # 3 = total non-rejected for our tenant (Paris High, Paris Low, Berlin).
    assert len(rows_mine) == 3


async def test_execute_spec_excludes_rejected_and_duplicate(
    db_session: AsyncSession,
) -> None:
    tenant, _ = await _seed_tenant_with_entities(db_session)
    spec = coerce_spec({"filter": {"city": "Paris"}, "summary": ""})
    rows = await execute_spec(db_session, tenant.id, spec)
    names = {r.name for r in rows}
    assert "Rejected Paris" not in names
    assert names == {"Cafe Paris High", "Cafe Paris Low"}


async def test_execute_spec_orders_by_quality_score_desc(
    db_session: AsyncSession,
) -> None:
    tenant, _ = await _seed_tenant_with_entities(db_session)
    spec = coerce_spec(
        {"filter": {"city": "Paris"}, "sort_by": "quality_score", "summary": ""}
    )
    rows = await execute_spec(db_session, tenant.id, spec)
    assert [r.name for r in rows] == ["Cafe Paris High", "Cafe Paris Low"]
