"""HTTP tests for the /jobs endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, Job
from app.db.session import get_session
from app.main import app


async def _override_dep(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


@pytest.fixture
def override_session(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    # Stop POST /jobs from launching the real pipeline in asyncio.create_task.
    from app.api import jobs as jobs_api

    async def _noop_bg(job_id) -> None:  # type: ignore[no-untyped-def]
        pass

    monkeypatch.setattr(jobs_api, "_run_in_background", _noop_bg)

    async def _dep() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _dep
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_jobs_creates_pending_job(db_session: AsyncSession, override_session) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs",
            json={"query": "restaurants in Paris", "limit": 50, "budget_cap_usd": 2.0},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["query_raw"] == "restaurants in Paris"
    assert body["limit"] == 50
    assert body["budget_cap_usd"] == 2.0
    assert body["entity_count"] == 0

    job = await db_session.get(Job, body["id"])
    assert job is not None
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_post_jobs_rejects_short_query(db_session: AsyncSession, override_session) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/jobs", json={"query": "x"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_job_returns_details(db_session: AsyncSession, override_session) -> None:
    job = Job(
        query_raw="cafes in Lisbon",
        limit=10,
        budget_cap_usd=1.0,
        status="succeeded",
        cost_usd=0.03,
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add(
        Entity(
            job_id=job.id,
            name="Café Something",
            domain="cafe.example.pt",
            field_sources={},
            external_ids={},
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["entity_count"] == 1
    assert body["query_raw"] == "cafes in Lisbon"


@pytest.mark.asyncio
async def test_get_job_not_found(db_session: AsyncSession, override_session) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_csv_for_completed_job(db_session: AsyncSession, override_session) -> None:
    job = Job(
        query_raw="restaurants in Paris",
        limit=5,
        budget_cap_usd=5.0,
        status="succeeded",
        cost_usd=0.05,
    )
    db_session.add(job)
    await db_session.flush()

    db_session.add(
        Entity(
            job_id=job.id,
            name="Le Petit Bistro",
            domain="lepetitbistro.example.fr",
            website="https://lepetitbistro.example.fr",
            email="contact@lepetitbistro.example.fr",
            phone="+33142000000",
            city="Paris",
            country="FR",
            category="restaurant",
            socials={"linkedin": "https://linkedin.com/company/bistro"},
            field_sources={
                "email": {"source": "crawler", "confidence": 0.9, "fetched_at": "x"},
                "phone": {"source": "crawler", "confidence": 0.9, "fetched_at": "x"},
            },
            external_ids={"google_place_id": "ChIJ_x"},
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert f"leadgen-{job.id}.csv" in resp.headers["content-disposition"]
    text = resp.text
    assert "name,website,email" in text.splitlines()[0]
    assert "Le Petit Bistro" in text
    assert "contact@lepetitbistro.example.fr" in text
    assert "https://linkedin.com/company/bistro" in text
    assert "ChIJ_x" in text
    assert "crawler" in text


@pytest.mark.asyncio
async def test_list_jobs_returns_newest_first_with_entity_counts(
    db_session: AsyncSession, override_session
) -> None:
    from datetime import UTC, datetime, timedelta

    t_old = datetime.now(UTC) - timedelta(hours=1)
    t_new = datetime.now(UTC)
    older = Job(
        query_raw="older",
        limit=10,
        budget_cap_usd=1.0,
        status="succeeded",
        created_at=t_old,
        updated_at=t_old,
    )
    newer = Job(
        query_raw="newer",
        limit=10,
        budget_cap_usd=1.0,
        status="running",
        created_at=t_new,
        updated_at=t_new,
    )
    db_session.add(older)
    db_session.add(newer)
    await db_session.flush()
    db_session.add(
        Entity(
            job_id=older.id,
            name="X",
            domain="x.example.com",
            field_sources={},
            external_ids={},
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert body["limit"] == 10
    assert body["offset"] == 0
    # Newest first. The first item we just inserted is `newer`.
    ordered = [item["query_raw"] for item in body["items"]]
    assert ordered.index("newer") < ordered.index("older")
    for item in body["items"]:
        if item["query_raw"] == "older":
            assert item["entity_count"] == 1
        if item["query_raw"] == "newer":
            assert item["entity_count"] == 0


@pytest.mark.asyncio
async def test_list_jobs_filter_by_status(db_session: AsyncSession, override_session) -> None:
    db_session.add(Job(query_raw="ok", limit=10, budget_cap_usd=1.0, status="succeeded"))
    db_session.add(Job(query_raw="run", limit=10, budget_cap_usd=1.0, status="running"))
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/jobs?status=running")
    body = resp.json()
    assert all(item["status"] == "running" for item in body["items"])
    assert body["total"] >= 1


@pytest.mark.asyncio
async def test_export_csv_rejected_while_running(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(query_raw="cafes in Lisbon", limit=10, budget_cap_usd=5.0, status="running")
    db_session.add(job)
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/export.csv")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_post_jobs_with_idempotency_key_creates_once(
    db_session: AsyncSession, override_session
) -> None:
    payload = {
        "query": "restaurants in Paris",
        "limit": 10,
        "budget_cap_usd": 2.0,
        "idempotency_key": "client-run-2026-04-17-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/jobs", json=payload)
        second = await client.post("/jobs", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    # Only one row in the DB with this key.
    from sqlalchemy import func, select

    count = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.idempotency_key == payload["idempotency_key"])
            )
        ).scalar_one()
    )
    assert count == 1


@pytest.mark.asyncio
async def test_post_jobs_different_keys_create_separate_jobs(
    db_session: AsyncSession, override_session
) -> None:
    base = {"query": "restaurants in Paris", "limit": 10, "budget_cap_usd": 2.0}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/jobs", json={**base, "idempotency_key": "run-alpha-001"})
        second = await client.post("/jobs", json={**base, "idempotency_key": "run-beta-002"})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.asyncio
async def test_post_jobs_without_idempotency_key_behaves_as_before(
    db_session: AsyncSession, override_session
) -> None:
    payload = {"query": "restaurants in Paris", "limit": 10, "budget_cap_usd": 2.0}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/jobs", json=payload)
        second = await client.post("/jobs", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.asyncio
async def test_post_jobs_rejects_too_short_idempotency_key(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs",
            json={
                "query": "restaurants in Paris",
                "idempotency_key": "short",
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_job_exposes_progress_fields(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(
        query_raw="cafes in Lisbon",
        limit=20,
        budget_cap_usd=2.0,
        status="running",
        places_discovered=10,
        places_processed=3,
    )
    db_session.add(job)
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["places_discovered"] == 10
    assert body["places_processed"] == 3
    assert body["progress_percent"] == 30.0


@pytest.mark.asyncio
async def test_post_jobs_bulk_creates_bulk_job(
    db_session: AsyncSession, override_session
) -> None:
    payload = {
        "entities": [
            {"name": "Foo Ltd", "website": "https://foo.example"},
            {"domain": "bar.example"},
        ],
        "budget_cap_usd": 3.0,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/jobs/bulk", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["limit"] == 2

    job = await db_session.get(Job, body["id"])
    assert job is not None
    assert job.job_type == "bulk_enrichment"
    assert job.seed_entities is not None
    assert len(job.seed_entities) == 2
    assert job.seed_entities[0]["website"] == "https://foo.example"
    assert job.seed_entities[1]["domain"] == "bar.example"


@pytest.mark.asyncio
async def test_post_jobs_bulk_rejects_row_without_website_or_domain(
    db_session: AsyncSession, override_session
) -> None:
    payload = {
        "entities": [
            {"name": "Missing Everything"},
        ],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/jobs/bulk", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_bulk_rejects_empty_list(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/jobs/bulk", json={"entities": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_bulk_csv_happy_path(
    db_session: AsyncSession, override_session
) -> None:
    csv_body = (
        "name,website,domain\n"
        "Foo Ltd,https://foo.example,foo.example\n"
        "Bar Ltd,,bar.example\n"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs/bulk/csv",
            files={"file": ("leads.csv", csv_body, "text/csv")},
            data={"budget_cap_usd": "3.0"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["limit"] == 2

    job = await db_session.get(Job, body["id"])
    assert job is not None
    assert job.job_type == "bulk_enrichment"
    assert job.seed_entities[0] == {
        "name": "Foo Ltd",
        "website": "https://foo.example",
        "domain": "foo.example",
    }
    assert job.seed_entities[1] == {
        "name": "Bar Ltd",
        "website": None,
        "domain": "bar.example",
    }


@pytest.mark.asyncio
async def test_post_jobs_bulk_csv_accepts_header_aliases(
    db_session: AsyncSession, override_session
) -> None:
    # "company" → name, "url" → website
    csv_body = "company,url\nAcme Corp,https://acme.example\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs/bulk/csv",
            files={"file": ("leads.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 201
    job = await db_session.get(Job, resp.json()["id"])
    assert job.seed_entities[0]["name"] == "Acme Corp"
    assert job.seed_entities[0]["website"] == "https://acme.example"


@pytest.mark.asyncio
async def test_post_jobs_bulk_csv_strips_bom_and_handles_blank_rows(
    db_session: AsyncSession, override_session
) -> None:
    # Excel-saved CSVs often have a UTF-8 BOM and trailing blank lines.
    csv_body = "\ufeffdomain\nfoo.example\n\n,\nbar.example\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs/bulk/csv",
            files={"file": ("leads.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 201
    job = await db_session.get(Job, resp.json()["id"])
    # Two valid rows; the blank line and the lone comma row are skipped.
    assert len(job.seed_entities) == 2
    assert {row["domain"] for row in job.seed_entities} == {"foo.example", "bar.example"}


@pytest.mark.asyncio
async def test_post_jobs_bulk_csv_rejects_csv_without_website_or_domain_column(
    db_session: AsyncSession, override_session
) -> None:
    csv_body = "name\nFoo\nBar\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs/bulk/csv",
            files={"file": ("leads.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_bulk_csv_rejects_empty_after_parse(
    db_session: AsyncSession, override_session
) -> None:
    csv_body = "website\n\n\n"  # header present, all rows blank
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs/bulk/csv",
            files={"file": ("leads.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_bulk_csv_rejects_non_utf8(
    db_session: AsyncSession, override_session
) -> None:
    # 0xff 0xfe is a UTF-16 BOM — can't decode as UTF-8.
    csv_body = b"\xff\xfesome junk"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs/bulk/csv",
            files={"file": ("leads.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_bulk_csv_rejects_oversized_upload(
    db_session: AsyncSession, override_session
) -> None:
    from app.api.jobs import MAX_CSV_BYTES

    huge = ("website\n" + ("https://x.example\n" * 100_000)).encode()
    assert len(huge) > MAX_CSV_BYTES
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/jobs/bulk/csv",
            files={"file": ("big.csv", huge, "text/csv")},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_list_job_entities_returns_paginated_rows(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(
        query_raw="cafes in Lisbon",
        limit=10,
        budget_cap_usd=1.0,
        status="succeeded",
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add_all(
        [
            Entity(
                job_id=job.id,
                name="High Score",
                domain="high.example.pt",
                quality_score=90,
                review_status="approved",
                field_sources={},
                external_ids={},
            ),
            Entity(
                job_id=job.id,
                name="Low Score",
                domain="low.example.pt",
                quality_score=40,
                review_status="review",
                field_sources={},
                external_ids={},
            ),
        ]
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    # Highest quality_score first.
    assert body["items"][0]["name"] == "High Score"
    assert body["items"][0]["review_status"] == "approved"


@pytest.mark.asyncio
async def test_list_job_entities_filters_by_review_status(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(query_raw="cafes", limit=10, budget_cap_usd=1.0, status="succeeded")
    db_session.add(job)
    await db_session.flush()
    db_session.add_all(
        [
            Entity(
                job_id=job.id,
                name="A",
                domain="a.example",
                review_status="approved",
                field_sources={},
                external_ids={},
            ),
            Entity(
                job_id=job.id,
                name="B",
                domain="b.example",
                review_status="review",
                field_sources={},
                external_ids={},
            ),
        ]
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/entities?review_status=review")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "B"


@pytest.mark.asyncio
async def test_list_job_entities_hides_duplicates_by_default(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(query_raw="cafes", limit=10, budget_cap_usd=1.0, status="succeeded")
    db_session.add(job)
    await db_session.flush()
    winner = Entity(
        job_id=job.id,
        name="Winner",
        domain="winner.example",
        field_sources={},
        external_ids={},
    )
    db_session.add(winner)
    await db_session.flush()
    db_session.add(
        Entity(
            job_id=job.id,
            name="Dup",
            domain="dup.example",
            duplicate_of=winner.id,
            field_sources={},
            external_ids={},
        )
    )
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}/entities")
        resp_all = await client.get(f"/jobs/{job.id}/entities?include_duplicates=true")
    assert resp.json()["total"] == 1
    assert resp_all.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_job_entities_404_when_job_missing(
    db_session: AsyncSession, override_session
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{uuid4()}/entities")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_job_progress_is_null_before_discovery(
    db_session: AsyncSession, override_session
) -> None:
    job = Job(
        query_raw="cafes in Lisbon",
        limit=20,
        budget_cap_usd=2.0,
        status="pending",
    )
    db_session.add(job)
    await db_session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/jobs/{job.id}")
    body = resp.json()
    assert body["places_discovered"] == 0
    assert body["places_processed"] == 0
    assert body["progress_percent"] is None
