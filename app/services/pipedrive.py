"""Pipedrive CRM client (minimal).

Pushes Entities as Pipedrive Persons. Auth uses the legacy `api_token`
query parameter — Pipedrive still supports this for personal API tokens,
and it's the simplest "paste a token" UX (no OAuth dance).

Pipedrive has no batch-create endpoint for persons, so we POST one row at
a time. A single 4xx/5xx is recorded against that row and the loop
continues — one bad row doesn't poison the export.

API docs: https://developers.pipedrive.com/docs/api/v1/Persons
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.pipedrive.com/v1"
PERSONS_ENDPOINT = "/persons"


@dataclass(slots=True)
class PipedriveExportResult:
    attempted: int
    created: int
    errors: list[str]


def base_url_for(company_domain: str | None) -> str:
    """Pipedrive recommends the company-scoped subdomain in production
    (some account-bound endpoints require it). Falls back to the global
    host when the tenant hasn't set one."""
    if company_domain:
        return f"https://{company_domain}.pipedrive.com/v1"
    return DEFAULT_BASE_URL


def entity_to_person_payload(entity: Any) -> dict[str, Any] | None:
    """Map an Entity to a Pipedrive Person create payload.

    Returns None when the entity has no email — without one, the Person
    can't be re-found or contacted, so pushing it is wasteful.

    Note: Pipedrive's Person.name is intended for an individual's name. We
    don't carry contact-person names yet, so we push the company name as a
    placeholder. v2 will create an Organization and attach the Person.
    """
    email = getattr(entity, "email", None)
    if not email:
        return None
    payload: dict[str, Any] = {
        "name": getattr(entity, "name", None) or email,
        "email": [{"value": email, "primary": True, "label": "work"}],
    }
    phone = getattr(entity, "phone", None)
    if phone:
        payload["phone"] = [{"value": phone, "primary": True, "label": "work"}]
    return payload


async def export_persons(
    api_token: str,
    entities: list[Any],
    *,
    company_domain: str | None = None,
    http: httpx.AsyncClient | None = None,
) -> PipedriveExportResult:
    """POST each mappable entity to Pipedrive's /persons.

    Per-row failures are recorded in `errors` and don't stop the loop.
    """
    payload_rows = [p for p in (entity_to_person_payload(e) for e in entities) if p]
    if not payload_rows:
        return PipedriveExportResult(attempted=0, created=0, errors=[])

    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=20.0)
    base = base_url_for(company_domain)
    params = {"api_token": api_token}

    attempted = len(payload_rows)
    created = 0
    errors: list[str] = []
    try:
        for payload in payload_rows:
            try:
                response = await client.post(
                    base + PERSONS_ENDPOINT,
                    json=payload,
                    params=params,
                )
                if response.status_code >= 400:
                    errors.append(
                        f"pipedrive {response.status_code}: {response.text[:200]}"
                    )
                    continue
                parsed = response.json()
                if parsed.get("success"):
                    created += 1
                else:
                    errors.append(f"pipedrive: {parsed.get('error') or 'unknown'}")
            except httpx.HTTPError as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if owns_http:
            await client.aclose()

    return PipedriveExportResult(attempted=attempted, created=created, errors=errors)
