"""Pydantic models for the slice of Foursquare Places API v3 we consume.

Only objective business facts (name, location, phone, website) are kept.
Foursquare-curated signals (tips, photos, stats, rating) are dropped before
they reach the entities table.

API docs: https://docs.foursquare.com/developer/reference/place-search
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FsqLocation(BaseModel):
    address: str | None = None
    address_extended: str | None = None
    locality: str | None = None  # city
    region: str | None = None
    postcode: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2
    formatted_address: str | None = None


class FsqPoint(BaseModel):
    latitude: float | None = None
    longitude: float | None = None


class FsqGeocodes(BaseModel):
    main: FsqPoint | None = None


class FsqPlace(BaseModel):
    """One entry from /places/search results."""

    fsq_id: str
    name: str
    location: FsqLocation | None = None
    geocodes: FsqGeocodes | None = None
    tel: str | None = None
    website: str | None = None
    # Foursquare returns a `categories` list; we intentionally ignore it to
    # keep curated content out of the entities table.
    categories: list[dict[str, object]] = Field(default_factory=list)
