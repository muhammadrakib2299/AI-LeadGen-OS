"""Tests for email syntax + MX verification."""

from __future__ import annotations

import types

import dns.exception
import dns.resolver
import pytest

from app.services import email_verify as ev
from app.services.email_verify import verify_email


def _mx_record(host: str, preference: int = 10) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        preference=preference, exchange=types.SimpleNamespace(to_text=lambda: host)
    )


class _FakeMxList(list):
    pass


def _mx_answer(*records: types.SimpleNamespace) -> _FakeMxList:
    fake = _FakeMxList(records)
    # Make str(rec.exchange) return the host
    return fake


async def test_verify_valid_email_returns_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(qname, rdtype, lifetime=5.0):
        assert rdtype == "MX"

        class R:
            preference = 10
            exchange = "mx.example.com."

        return [R()]

    monkeypatch.setattr(ev.dns.asyncresolver, "resolve", fake_resolve)
    result = await verify_email("hello@example.com")
    assert result.status == "valid"
    assert result.mx_host == "mx.example.com"
    assert result.confidence_boost > 1.0


async def test_verify_invalid_syntax() -> None:
    result = await verify_email("not-an-email")
    assert result.status == "invalid_syntax"
    assert result.confidence_boost == 0.0


async def test_verify_nxdomain_returns_no_mx(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(qname, rdtype, lifetime=5.0):
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(ev.dns.asyncresolver, "resolve", fake_resolve)
    result = await verify_email("hello@example-does-not-exist.com")
    assert result.status == "no_mx"
    assert result.confidence_boost < 1.0


async def test_verify_dns_timeout_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(qname, rdtype, lifetime=5.0):
        raise dns.exception.Timeout()

    monkeypatch.setattr(ev.dns.asyncresolver, "resolve", fake_resolve)
    result = await verify_email("hi@example.com")
    assert result.status == "unreachable"
    assert result.confidence_boost == 0.9


async def test_verify_unexpected_exception_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(qname, rdtype, lifetime=5.0):
        raise RuntimeError("resolver crashed")

    monkeypatch.setattr(ev.dns.asyncresolver, "resolve", fake_resolve)
    result = await verify_email("hi@example.com")
    assert result.status == "unreachable"
    assert "resolver crashed" in (result.reason or "")
