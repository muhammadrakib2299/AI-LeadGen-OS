"""Compiled regex + noise filters used by the contact extractors."""

from __future__ import annotations

import re

# RFC 5322-ish (lenient). We filter noise downstream rather than tightening here.
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)

# International phone numbers: optional +, country code, separators, 7-15 digits total.
# Kept liberal; normalization strips out clearly-invalid results.
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?"
    r"(?:\(\d{1,4}\)[\s.\-]?)?"
    r"\d{2,4}[\s.\-]?\d{2,4}[\s.\-]?\d{2,5}",
)

# Domains that produce false-positive emails (asset pipelines, analytics, etc.)
EMAIL_DOMAIN_BLOCKLIST = frozenset(
    {
        "sentry.io",
        "sentry-next.io",
        "wixpress.com",
        "cloudfront.net",
        "amazonaws.com",
        "googleusercontent.com",
        "gstatic.com",
        "bootstrapcdn.com",
        "jsdelivr.net",
        "unpkg.com",
        "example.com",
    }
)

# Extensions that look like image assets appearing before @ — reject the whole match.
EMAIL_LOCAL_BLOCKLIST_SUFFIX = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
)

SOCIAL_HOST_MAP = {
    "linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}


def is_noisy_email(addr: str) -> bool:
    addr_lower = addr.lower()
    local, _, domain = addr_lower.partition("@")
    if not local or not domain:
        return True
    if domain in EMAIL_DOMAIN_BLOCKLIST:
        return True
    if any(local.endswith(suf) for suf in EMAIL_LOCAL_BLOCKLIST_SUFFIX):
        return True
    # Long hex-ish locals (e.g. cache-busting hashes) are almost always false positives.
    return len(local) >= 24 and re.fullmatch(r"[0-9a-f]+", local) is not None


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("+"):
        body = digits[1:]
        if 7 <= len(body) <= 15 and body.isdigit():
            return "+" + body
        return None
    if digits.isdigit() and 7 <= len(digits) <= 15:
        return digits
    return None
