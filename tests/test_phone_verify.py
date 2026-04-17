"""Tests for phone parse + E.164 normalization."""

from __future__ import annotations

from app.services.phone_verify import verify_phone


def test_verify_valid_national_number_with_region() -> None:
    result = verify_phone("01 42 00 00 00", region="FR")
    assert result.status == "valid"
    assert result.e164 == "+33142000000"
    assert result.region == "FR"
    assert result.kind in ("fixed_line", "fixed_or_mobile")
    assert result.confidence_boost > 1.0


def test_verify_e164_without_region() -> None:
    result = verify_phone("+442071838750")
    assert result.status == "valid"
    assert result.e164 == "+442071838750"
    assert result.region == "GB"


def test_verify_mobile_classified_as_mobile() -> None:
    # 06/07 prefixes in France are mobile allocations.
    result = verify_phone("+33612345678")
    assert result.status == "valid"
    assert result.kind == "mobile"


def test_verify_national_digits_without_region_is_unparseable() -> None:
    result = verify_phone("01 42 00 00 00")
    assert result.status == "unparseable"
    assert result.confidence_boost == 0.0


def test_verify_empty_string_is_unparseable() -> None:
    result = verify_phone("   ")
    assert result.status == "unparseable"
    assert result.reason == "empty"


def test_verify_too_short_number_is_invalid_or_unparseable() -> None:
    result = verify_phone("+3312", region="FR")
    # libphonenumber rejects short numbers either at parse or validate time.
    assert result.status in ("invalid", "unparseable")
    assert result.confidence_boost < 1.0


def test_verify_too_long_number_is_invalid() -> None:
    # Extra digits on an otherwise FR-shaped number.
    result = verify_phone("+331420000000123", region="FR")
    assert result.status in ("invalid", "possible", "unparseable")
    assert result.confidence_boost < 1.0
