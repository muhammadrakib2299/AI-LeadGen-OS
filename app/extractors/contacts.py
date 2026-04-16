"""ContactsExtractor: regex + HTML first, LLM only on empty results.

Per overview.md §5: "regex + 1 cheap LLM call for email/phone/social extraction."
The cheap call is skipped entirely when regex/HTML already found something.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.extractors.html import extract_from_html
from app.extractors.patterns import is_noisy_email, normalize_phone
from app.models.contacts import ExtractedContacts, Social, SocialPlatform
from app.services.crawler import CrawlResult
from app.services.llm import LLMClient

log = get_logger(__name__)


MAX_LLM_HTML_CHARS = 12_000  # keep prompt size bounded
_SOCIAL_VALUES: frozenset[str] = frozenset(
    {"linkedin", "twitter", "facebook", "instagram", "youtube", "tiktok", "other"}
)


LLM_SYSTEM = """You extract business contact information from a company website.

Return strictly one JSON object:
{
  "emails": ["business@example.com", ...],
  "phones": ["+33140000000", ...],
  "socials": [{"platform": "linkedin|twitter|facebook|instagram|youtube|tiktok|other",
               "url": "https://..."}]
}

Rules:
- Only include CONTACT info for the BUSINESS itself, not third-party widgets or
  employee personal accounts.
- Phones must be formatted E.164 when possible (leading + and country code).
- Never invent information that is not present. Empty arrays are fine.
- Output JSON only, no prose, no code fences.
"""


class ContactsExtractor:
    """Orchestrates regex/HTML extraction and an optional LLM fallback."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm

    async def extract(self, pages: list[CrawlResult]) -> ExtractedContacts:
        emails: list[str] = []
        phones: list[str] = []
        socials: list[Social] = []
        source_urls: list[str] = []

        for page in pages:
            if page.html is None or page.status >= 400:
                continue
            source_urls.append(page.url)
            page_emails, page_phones, page_socials = extract_from_html(page.html)
            emails.extend(page_emails)
            phones.extend(page_phones)
            socials.extend(page_socials)

        result = ExtractedContacts(
            emails=_dedupe_ci(emails),
            phones=_dedupe_ci(phones),
            socials=_dedupe_socials(socials),
            source_urls=source_urls,
        )

        if not result.is_empty() or self._llm is None:
            return result

        # LLM fallback — only when regex/HTML produced nothing.
        combined_html = _join_html_for_llm(pages)
        if not combined_html:
            return result

        try:
            raw = await self._llm.complete_json(
                system=LLM_SYSTEM,
                user=combined_html,
                max_tokens=1024,
            )
        except Exception as exc:
            log.warning("contacts_extractor_llm_failed", error=str(exc))
            return result

        llm_emails = _clean_emails(raw.get("emails") or [])
        llm_phones = _clean_phones(raw.get("phones") or [])
        llm_socials = _clean_socials(raw.get("socials") or [])

        return ExtractedContacts(
            emails=llm_emails,
            phones=llm_phones,
            socials=llm_socials,
            source_urls=source_urls,
            used_llm=True,
        )


def _join_html_for_llm(pages: list[CrawlResult]) -> str:
    parts: list[str] = []
    budget = MAX_LLM_HTML_CHARS
    for p in pages:
        if p.html is None or p.status >= 400:
            continue
        snippet = p.html[:budget]
        if not snippet:
            break
        parts.append(f"# {p.url}\n{snippet}")
        budget -= len(snippet)
        if budget <= 0:
            break
    return "\n\n".join(parts)


def _clean_emails(raw: list[Any]) -> list[str]:
    out: list[str] = []
    for e in raw:
        if not isinstance(e, str):
            continue
        stripped = e.strip()
        if stripped and "@" in stripped and not is_noisy_email(stripped):
            out.append(stripped)
    return _dedupe_ci(out)


def _clean_phones(raw: list[Any]) -> list[str]:
    out: list[str] = []
    for p in raw:
        if not isinstance(p, str):
            continue
        n = normalize_phone(p)
        if n is not None:
            out.append(n)
    return _dedupe_ci(out)


def _clean_socials(raw: list[Any]) -> list[Social]:
    out: list[Social] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        platform = s.get("platform")
        url = s.get("url")
        if not isinstance(platform, str) or not isinstance(url, str):
            continue
        platform_l = platform.lower().strip()
        if platform_l not in _SOCIAL_VALUES:
            platform_l = "other"
        out.append(Social(platform=platform_l, url=url.strip()))  # type: ignore[arg-type]
    return _dedupe_socials(out)


def _dedupe_ci(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        key = i.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out


def _dedupe_socials(items: list[Social]) -> list[Social]:
    seen: set[tuple[SocialPlatform, str]] = set()
    out: list[Social] = []
    for s in items:
        key = (s.platform, s.url.rstrip("/").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out
