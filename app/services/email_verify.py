"""Email verification — syntax (RFC 5322-ish) + DNS MX reachability.

Stays deliberately lightweight:
- Syntax via `email_validator` (already a dependency)
- MX lookup via `dnspython.asyncresolver` — no SMTP probe (those trip
  anti-spam heuristics and draw complaints)

Hunter.io / ZeroBounce hooks belong one layer above this — they are paid
services and should only fire for top-value leads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import dns.asyncresolver
import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from app.core.circuit import CircuitBreaker, CircuitOpenError

VerificationStatus = Literal[
    "valid",
    "invalid_syntax",
    "no_mx",
    "unreachable",
]


_DNS_BREAKER = CircuitBreaker(
    name="dns_mx",
    failure_threshold=10,  # DNS is flakier; tolerate more before opening
    cooldown_s=30.0,
    expected_exceptions=(dns.exception.Timeout, dns.resolver.NoNameservers),
)


@dataclass(slots=True)
class EmailVerification:
    email: str
    status: VerificationStatus
    mx_host: str | None = None
    reason: str | None = None

    @property
    def confidence_boost(self) -> float:
        """Multiplier applied to the extracted email's confidence."""
        return {
            "valid": 1.05,
            "invalid_syntax": 0.0,
            "no_mx": 0.4,
            "unreachable": 0.9,
        }[self.status]


async def verify_email(email: str, *, dns_timeout_s: float = 5.0) -> EmailVerification:
    try:
        parsed = validate_email(email, check_deliverability=False)
        normalized = parsed.normalized
    except EmailNotValidError as exc:
        return EmailVerification(email=email, status="invalid_syntax", reason=str(exc))

    domain = normalized.split("@", 1)[1]

    async def _resolve_mx() -> list[Any]:
        return await dns.asyncresolver.resolve(domain, "MX", lifetime=dns_timeout_s)

    try:
        answers = await _DNS_BREAKER.call(_resolve_mx)
    except CircuitOpenError as exc:
        return EmailVerification(email=normalized, status="unreachable", reason=str(exc))
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN) as exc:
        return EmailVerification(email=normalized, status="no_mx", reason=type(exc).__name__)
    except dns.exception.Timeout:
        return EmailVerification(email=normalized, status="unreachable", reason="DNS timeout")
    except Exception as exc:
        return EmailVerification(
            email=normalized, status="unreachable", reason=f"{type(exc).__name__}: {exc}"
        )

    try:
        lowest = min(answers, key=lambda r: r.preference)
        mx_host = str(lowest.exchange).rstrip(".") or None
    except ValueError:
        mx_host = None
    return EmailVerification(email=normalized, status="valid", mx_host=mx_host)
