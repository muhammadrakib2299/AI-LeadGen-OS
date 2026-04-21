"""Pydantic models for the slice of Yelp Fusion /businesses/search we consume.

Only the fields that are derivable facts about a business (name, address,
phone, coords) are modeled. Yelp's curated content (ratings, reviews, price,
categories taxonomy, image URLs) is deliberately NOT stored beyond 24h per
the Fusion ToS — we don't pull those into the entity columns at all.

API docs: https://docs.developer.yelp.com/reference/v3_business_search
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class YelpLocation(BaseModel):
    address1: str | None = None
    address2: str | None = None
    address3: str | None = None
    city: str | None = None
    zip_code: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2
    state: str | None = None
    display_address: list[str] = Field(default_factory=list)


class YelpCoordinates(BaseModel):
    latitude: float | None = None
    longitude: float | None = None


class YelpBusiness(BaseModel):
    """One entry from /businesses/search `businesses`."""

    id: str
    alias: str | None = None
    name: str
    phone: str | None = None
    display_phone: str | None = None
    location: YelpLocation | None = None
    coordinates: YelpCoordinates | None = None
    is_closed: bool | None = None

    @property
    def formatted_address(self) -> str | None:
        if self.location and self.location.display_address:
            return ", ".join([p for p in self.location.display_address if p])
        if self.location:
            bits = [
                self.location.address1,
                self.location.city,
                self.location.zip_code,
                self.location.country,
            ]
            joined = ", ".join([b for b in bits if b])
            return joined or None
        return None
