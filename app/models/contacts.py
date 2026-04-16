"""Structured result of contact-info extraction from one or more web pages."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SocialPlatform = Literal[
    "linkedin",
    "twitter",
    "facebook",
    "instagram",
    "youtube",
    "tiktok",
    "other",
]


class Social(BaseModel):
    platform: SocialPlatform
    url: str


class ExtractedContacts(BaseModel):
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    socials: list[Social] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    used_llm: bool = False

    def is_empty(self) -> bool:
        return not (self.emails or self.phones or self.socials)
