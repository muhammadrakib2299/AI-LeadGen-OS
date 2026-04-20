"""Pydantic models for the OpenCorporates /companies/search response slice.

OpenCorporates returns far more fields than we need. We keep just the ones
that enrich an entity with verifiable legal-entity data (legal name, number,
jurisdiction, address, status) — the rest stays in `raw_fetches.payload`
for auditability.
"""

from __future__ import annotations

from pydantic import BaseModel


class CompanyRecord(BaseModel):
    """A single company hit from /companies/search results."""

    opencorporates_id: str
    name: str
    company_number: str | None = None
    jurisdiction_code: str | None = None
    registered_address: str | None = None
    incorporation_date: str | None = None
    company_type: str | None = None
    current_status: str | None = None
    opencorporates_url: str | None = None
