"""HubSpot CRM client (minimal).

Only the slice we need: Contacts batch create. Private-app tokens
(pat-xxxx-...) are sent as `Authorization: Bearer <token>` — same shape as
OAuth access tokens, simpler to manage.

API docs: https://developers.hubspot.com/docs/api/crm/contacts
Batch create: POST /crm/v3/objects/contacts/batch/create (max 100 per call).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

HUBSPOT_BASE_URL = "https://api.hubapi.com"
CONTACTS_BATCH_CREATE = "/crm/v3/objects/contacts/batch/create"
MAX_BATCH = 100


@dataclass(slots=True)
class HubspotExportResult:
    attempted: int
    created: int
    errors: list[str]


def entity_to_contact_properties(entity: Any) -> dict[str, str] | None:
    """Map an Entity row to HubSpot Contact properties.

    Returns None when the entity has no email — HubSpot treats email as the
    primary identifier; pushing contacts without one is wasteful. Fields
    that can't be mapped cleanly (rating, category taxonomy) are skipped.
    """
    if not getattr(entity, "email", None):
        return None

    props: dict[str, str] = {"email": entity.email}
    if getattr(entity, "name", None):
        props["company"] = entity.name
    if getattr(entity, "phone", None):
        props["phone"] = entity.phone
    if getattr(entity, "website", None):
        props["website"] = entity.website
    if getattr(entity, "city", None):
        props["city"] = entity.city
    if getattr(entity, "country", None):
        props["country"] = entity.country
    return props


async def export_contacts(
    access_token: str,
    entities: list[Any],
    *,
    http: httpx.AsyncClient | None = None,
) -> HubspotExportResult:
    """Push the given entities to HubSpot as Contacts. Batches to 100 per call."""
    payload_rows = [p for p in (entity_to_contact_properties(e) for e in entities) if p]
    if not payload_rows:
        return HubspotExportResult(attempted=0, created=0, errors=[])

    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=20.0)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    attempted = len(payload_rows)
    created = 0
    errors: list[str] = []
    try:
        for start in range(0, len(payload_rows), MAX_BATCH):
            chunk = payload_rows[start : start + MAX_BATCH]
            body = {"inputs": [{"properties": props} for props in chunk]}
            try:
                response = await client.post(
                    HUBSPOT_BASE_URL + CONTACTS_BATCH_CREATE,
                    json=body,
                    headers=headers,
                )
                if response.status_code >= 400:
                    errors.append(
                        f"hubspot {response.status_code}: {response.text[:200]}"
                    )
                    continue
                parsed = response.json()
                # 201 with {"results": [...], "status": "COMPLETE"} on success.
                created += len(parsed.get("results") or chunk)
            except httpx.HTTPError as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if owns_http:
            await client.aclose()

    return HubspotExportResult(attempted=attempted, created=created, errors=errors)
