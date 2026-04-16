"""CSV export for job results."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from app.db.models import Entity

CSV_COLUMNS: tuple[str, ...] = (
    "name",
    "website",
    "email",
    "phone",
    "address",
    "city",
    "country",
    "category",
    "socials_linkedin",
    "socials_twitter",
    "socials_facebook",
    "socials_instagram",
    "socials_youtube",
    "google_place_id",
    "email_source",
    "phone_source",
)


def entities_to_csv(entities: Sequence[Entity]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for ent in entities:
        writer.writerow(_row(ent))
    return buf.getvalue()


def _row(ent: Entity) -> dict[str, str]:
    socials = ent.socials or {}
    field_sources = ent.field_sources or {}
    external_ids = ent.external_ids or {}
    return {
        "name": ent.name or "",
        "website": ent.website or "",
        "email": ent.email or "",
        "phone": ent.phone or "",
        "address": ent.address or "",
        "city": ent.city or "",
        "country": ent.country or "",
        "category": ent.category or "",
        "socials_linkedin": socials.get("linkedin", ""),
        "socials_twitter": socials.get("twitter", ""),
        "socials_facebook": socials.get("facebook", ""),
        "socials_instagram": socials.get("instagram", ""),
        "socials_youtube": socials.get("youtube", ""),
        "google_place_id": external_ids.get("google_place_id", ""),
        "email_source": (field_sources.get("email") or {}).get("source", ""),
        "phone_source": (field_sources.get("phone") or {}).get("source", ""),
    }
