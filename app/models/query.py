"""Query request and validated-query models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    """Raw user input. The only required field is `query`."""

    query: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=100, ge=1, le=1000)

    @field_validator("query")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class ValidatedQuery(BaseModel):
    """Structured, pipeline-ready query produced by the validator."""

    entity_type: str = Field(min_length=2, max_length=64)
    city: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=128)
    country: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}$",
        description="ISO 3166-1 alpha-2 country code.",
    )
    keywords: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=100, ge=1, le=1000)

    confidence: float = Field(ge=0.0, le=1.0)

    def has_sufficient_location(self) -> bool:
        return bool(self.city or self.region or self.country)


class QueryValidationError(BaseModel):
    """Returned when a query is too vague or unsupported."""

    status: Literal["rejected"] = "rejected"
    reason: str
    suggestions: list[str] = Field(default_factory=list)
