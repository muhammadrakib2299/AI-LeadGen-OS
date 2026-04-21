"""Tests for Stripe billing event handling.

Uses the real module-level helpers (no stripe SDK calls) so no API keys
are needed. Full HTTP-level tests would require a DB + mocked Stripe
session; keeping this layer fast and pure.
"""

from __future__ import annotations

from app.api.billing import _plan_from_subscription_status, _tenant_id_from_metadata


def test_plan_mapping_active() -> None:
    assert _plan_from_subscription_status("active") == "standard"
    assert _plan_from_subscription_status("trialing") == "standard"


def test_plan_mapping_past_due() -> None:
    assert _plan_from_subscription_status("past_due") == "past_due"
    assert _plan_from_subscription_status("unpaid") == "past_due"


def test_plan_mapping_terminal() -> None:
    assert _plan_from_subscription_status("canceled") == "canceled"
    assert _plan_from_subscription_status("incomplete_expired") == "canceled"


def test_plan_mapping_unknown_defaults_to_free() -> None:
    assert _plan_from_subscription_status("something_new") == "free"
    assert _plan_from_subscription_status(None) == "free"


def test_tenant_id_from_client_reference_id() -> None:
    tid = _tenant_id_from_metadata(
        {"client_reference_id": "12345678-1234-5678-1234-567812345678"}
    )
    assert tid is not None
    assert str(tid) == "12345678-1234-5678-1234-567812345678"


def test_tenant_id_from_metadata_field() -> None:
    tid = _tenant_id_from_metadata(
        {"metadata": {"tenant_id": "12345678-1234-5678-1234-567812345678"}}
    )
    assert tid is not None


def test_tenant_id_none_when_absent() -> None:
    assert _tenant_id_from_metadata({}) is None


def test_tenant_id_none_when_malformed() -> None:
    assert _tenant_id_from_metadata({"client_reference_id": "not-a-uuid"}) is None
    assert _tenant_id_from_metadata({"metadata": {"tenant_id": "nope"}}) is None
