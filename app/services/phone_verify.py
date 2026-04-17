"""Phone verification — parse, normalize to E.164, classify line type.

Uses Google's libphonenumber via the `phonenumbers` package. Region hint
matters a lot: the same digits "01 42 00 00 00" are a valid Paris landline
in FR but meaningless without a country. Callers pass the ISO-3166 region
when known (from Places / validated query) and fall back to None for
pre-formatted international numbers starting with "+".

No network calls — this is deterministic parsing. Carrier/HLR lookups are
paid services and belong one layer up for top-value leads only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType

PhoneStatus = Literal[
    "valid",
    "possible",
    "invalid",
    "unparseable",
]

PhoneKind = Literal[
    "mobile",
    "fixed_line",
    "fixed_or_mobile",
    "voip",
    "toll_free",
    "premium_rate",
    "shared_cost",
    "pager",
    "uan",
    "voicemail",
    "unknown",
]


_KIND_MAP: dict[int, PhoneKind] = {
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE: "fixed_line",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.TOLL_FREE: "toll_free",
    PhoneNumberType.PREMIUM_RATE: "premium_rate",
    PhoneNumberType.SHARED_COST: "shared_cost",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "uan",
    PhoneNumberType.VOICEMAIL: "voicemail",
    PhoneNumberType.UNKNOWN: "unknown",
}


@dataclass(slots=True)
class PhoneVerification:
    input: str
    status: PhoneStatus
    e164: str | None = None
    region: str | None = None
    kind: PhoneKind = "unknown"
    reason: str | None = None

    @property
    def confidence_boost(self) -> float:
        """Multiplier applied to the extracted phone's confidence."""
        return {
            "valid": 1.05,
            "possible": 0.75,
            "invalid": 0.25,
            "unparseable": 0.0,
        }[self.status]


def verify_phone(raw: str, *, region: str | None = None) -> PhoneVerification:
    """Parse `raw` against optional ISO-3166 alpha-2 `region` (e.g. "FR").

    International numbers starting with "+" parse without a region; national
    numbers like "01 42 00 00 00" require one.
    """
    stripped = raw.strip()
    if not stripped:
        return PhoneVerification(input=raw, status="unparseable", reason="empty")

    region_hint = region.upper() if region else None
    try:
        number = phonenumbers.parse(stripped, region_hint)
    except NumberParseException as exc:
        return PhoneVerification(input=raw, status="unparseable", reason=str(exc))

    inferred_region = phonenumbers.region_code_for_number(number)
    kind = _KIND_MAP.get(phonenumbers.number_type(number), "unknown")
    e164 = phonenumbers.format_number(number, PhoneNumberFormat.E164)

    if phonenumbers.is_valid_number(number):
        return PhoneVerification(
            input=raw,
            status="valid",
            e164=e164,
            region=inferred_region,
            kind=kind,
        )
    if phonenumbers.is_possible_number(number):
        return PhoneVerification(
            input=raw,
            status="possible",
            e164=e164,
            region=inferred_region,
            kind=kind,
            reason="possible_but_not_valid",
        )
    return PhoneVerification(
        input=raw,
        status="invalid",
        e164=e164,
        region=inferred_region,
        kind=kind,
        reason="failed_validation",
    )
