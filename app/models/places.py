"""Pydantic models for the slice of Google Places API (New) we consume."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlaceLocation(BaseModel):
    latitude: float
    longitude: float


class PlaceDisplayName(BaseModel):
    text: str
    language_code: str | None = Field(default=None, alias="languageCode")


class PlaceAddressComponent(BaseModel):
    long_text: str = Field(alias="longText")
    short_text: str = Field(alias="shortText")
    types: list[str] = Field(default_factory=list)


class Place(BaseModel):
    """One entry from a Places Text Search response."""

    id: str
    display_name: PlaceDisplayName | None = Field(default=None, alias="displayName")
    formatted_address: str | None = Field(default=None, alias="formattedAddress")
    address_components: list[PlaceAddressComponent] = Field(
        default_factory=list, alias="addressComponents"
    )
    location: PlaceLocation | None = None
    types: list[str] = Field(default_factory=list)
    primary_type: str | None = Field(default=None, alias="primaryType")
    website_uri: str | None = Field(default=None, alias="websiteUri")
    national_phone_number: str | None = Field(default=None, alias="nationalPhoneNumber")
    international_phone_number: str | None = Field(default=None, alias="internationalPhoneNumber")
    rating: float | None = None
    user_rating_count: int | None = Field(default=None, alias="userRatingCount")

    model_config = {"populate_by_name": True}

    @property
    def name(self) -> str:
        return self.display_name.text if self.display_name else ""

    def country_code(self) -> str | None:
        for comp in self.address_components:
            if "country" in comp.types:
                return comp.short_text.upper() if comp.short_text else None
        return None

    def city(self) -> str | None:
        for t in ("locality", "postal_town", "administrative_area_level_2"):
            for comp in self.address_components:
                if t in comp.types:
                    return comp.long_text
        return None
