"""Tests for the contact extractors."""

from __future__ import annotations

from typing import Any

from app.extractors.contacts import ContactsExtractor
from app.extractors.html import extract_from_html
from app.extractors.patterns import is_noisy_email, normalize_phone
from app.models.contacts import ExtractedContacts
from app.services.crawler import CrawlResult


def _page(url: str, html: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        status=200,
        content_type="text/html",
        html=html,
        duration_ms=1,
        content_hash=None,
    )


def test_is_noisy_email_catches_asset_filenames() -> None:
    assert is_noisy_email("logo.png@cdn.example.com")
    assert is_noisy_email("notify@sentry.io")
    assert not is_noisy_email("hello@example.fr")


def test_normalize_phone_keeps_e164_and_rejects_garbage() -> None:
    assert normalize_phone("+33 1 42 00 00 00") == "+33142000000"
    assert normalize_phone("02012345678") == "02012345678"
    assert normalize_phone("abc") is None
    assert normalize_phone("12") is None


def test_extract_from_html_picks_up_mailto_tel_and_socials() -> None:
    html = """
    <html><body>
      <a href="mailto:hello@example.fr">Mail us</a>
      <a href="tel:+33142000000">Phone</a>
      <a href="https://www.linkedin.com/company/le-petit-bistro">LinkedIn</a>
      <a href="https://twitter.com/petitbistro">Twitter</a>
      <p>Contact: bookings@example.fr</p>
    </body></html>
    """
    emails, phones, socials = extract_from_html(html)
    assert "hello@example.fr" in emails
    assert "bookings@example.fr" in emails
    assert phones == ["+33142000000"]
    platforms = {s.platform for s in socials}
    assert {"linkedin", "twitter"}.issubset(platforms)


def test_extract_from_html_ignores_noisy_emails() -> None:
    html = """
    <html><body>
      <img src="logo.png">
      <a href="mailto:hello@sentry.io">analytics</a>
      <a href="mailto:real@example.co.uk">real</a>
    </body></html>
    """
    emails, _, _ = extract_from_html(html)
    assert emails == ["real@example.co.uk"]


def test_extract_from_html_dedupes_case_insensitive() -> None:
    html = """
    <a href="mailto:Hello@Example.fr">h</a>
    <p>contact hello@example.fr or HELLO@example.fr</p>
    """
    emails, _, _ = extract_from_html(html)
    assert len(emails) == 1


async def test_contacts_extractor_uses_regex_and_skips_llm_when_found() -> None:
    class DummyLLM:
        calls = 0

        async def complete_json(
            self, system: str, user: str, *, model: str = "", max_tokens: int = 0
        ) -> dict[str, Any]:
            DummyLLM.calls += 1
            return {}

    pages = [_page("https://example.fr/contact", '<a href="mailto:hi@example.fr">hi</a>')]
    extractor = ContactsExtractor(llm=DummyLLM())
    result = await extractor.extract(pages)
    assert isinstance(result, ExtractedContacts)
    assert result.emails == ["hi@example.fr"]
    assert result.used_llm is False
    assert DummyLLM.calls == 0


async def test_contacts_extractor_falls_back_to_llm_when_empty() -> None:
    class SuccessLLM:
        async def complete_json(
            self, system: str, user: str, *, model: str = "", max_tokens: int = 0
        ) -> dict[str, Any]:
            return {
                "emails": ["found@example.fr"],
                "phones": ["+33 1 42 00 00 00"],
                "socials": [
                    {"platform": "LinkedIn", "url": "https://linkedin.com/company/x/"},
                    {"platform": "NotAPlatform", "url": "https://random.example"},
                ],
            }

    pages = [_page("https://example.fr/", "<html><body>no contacts here</body></html>")]
    extractor = ContactsExtractor(llm=SuccessLLM())
    result = await extractor.extract(pages)
    assert result.emails == ["found@example.fr"]
    assert result.phones == ["+33142000000"]
    assert result.used_llm is True
    platforms = {s.platform for s in result.socials}
    assert "linkedin" in platforms
    assert "other" in platforms  # unknown platform bucketed


async def test_contacts_extractor_llm_failure_returns_empty_not_raise() -> None:
    class BrokenLLM:
        async def complete_json(
            self, system: str, user: str, *, model: str = "", max_tokens: int = 0
        ) -> dict[str, Any]:
            raise RuntimeError("anthropic down")

    pages = [_page("https://example.fr/", "<html><body>no contacts</body></html>")]
    extractor = ContactsExtractor(llm=BrokenLLM())
    result = await extractor.extract(pages)
    assert result.is_empty()
    assert result.used_llm is False


async def test_contacts_extractor_with_no_llm_returns_empty_when_nothing_found() -> None:
    pages = [_page("https://example.fr/", "<html><body>no contacts</body></html>")]
    extractor = ContactsExtractor(llm=None)
    result = await extractor.extract(pages)
    assert result.is_empty()
    assert result.used_llm is False


async def test_contacts_extractor_skips_failed_pages() -> None:
    pages = [
        CrawlResult(
            url="https://example.fr/",
            status=500,
            content_type="text/html",
            html=None,
            duration_ms=1,
            content_hash=None,
        ),
        _page("https://example.fr/contact", '<a href="mailto:hi@example.fr">hi</a>'),
    ]
    extractor = ContactsExtractor(llm=None)
    result = await extractor.extract(pages)
    assert result.emails == ["hi@example.fr"]
    assert result.source_urls == ["https://example.fr/contact"]
