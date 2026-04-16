"""HTML-aware extraction: pull mailto:/tel:/social links then regex over text."""

from __future__ import annotations

from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from app.extractors.patterns import (
    EMAIL_RE,
    PHONE_RE,
    SOCIAL_HOST_MAP,
    is_noisy_email,
    normalize_phone,
)
from app.models.contacts import Social, SocialPlatform


def extract_from_html(html: str) -> tuple[list[str], list[str], list[Social]]:
    """Return (emails, phones, socials) extracted from a single HTML string."""
    tree = HTMLParser(html)

    emails: list[str] = []
    phones: list[str] = []
    socials: list[Social] = []

    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        lower = href.lower()
        if lower.startswith("mailto:"):
            addr = href[7:].split("?", 1)[0].strip()
            if addr:
                emails.append(addr)
            continue
        if lower.startswith("tel:"):
            raw = href[4:].split("?", 1)[0].strip()
            normalized = normalize_phone(raw)
            if normalized:
                phones.append(normalized)
            continue
        platform = _classify_social(href)
        if platform is not None:
            socials.append(Social(platform=platform, url=href))

    # Free-text fallback — strip scripts/styles before matching.
    for tag in tree.css("script, style, noscript"):
        tag.decompose()
    text = tree.text(separator=" ")
    for m in EMAIL_RE.findall(text):
        emails.append(m)
    for m in PHONE_RE.findall(text):
        normalized = normalize_phone(m)
        if normalized:
            phones.append(normalized)

    emails = _dedupe([e for e in (e.strip() for e in emails) if e and not is_noisy_email(e)])
    phones = _dedupe(phones)
    socials = _dedupe_socials(socials)
    return emails, phones, socials


def _classify_social(url: str) -> SocialPlatform | None:
    try:
        host = (urlparse(url).netloc or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    host = host.removeprefix("www.")
    for known, platform in SOCIAL_HOST_MAP.items():
        if host == known or host.endswith("." + known):
            return platform  # type: ignore[return-value]
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        lower = item.lower()
        if lower in seen:
            continue
        seen.add(lower)
        out.append(item)
    return out


def _dedupe_socials(items: list[Social]) -> list[Social]:
    seen: set[tuple[str, str]] = set()
    out: list[Social] = []
    for s in items:
        key = (s.platform, s.url.rstrip("/").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out
