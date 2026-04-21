"""Tests for the optional OpenTelemetry wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core import telemetry


def test_configure_telemetry_skips_without_endpoint() -> None:
    telemetry._INITIALIZED = False
    with patch("app.core.telemetry.get_settings") as mock:
        mock.return_value.otel_exporter_otlp_endpoint = None
        assert telemetry.configure_telemetry(app=MagicMock()) is False
    assert telemetry._INITIALIZED is False


def test_configure_telemetry_is_idempotent() -> None:
    # Pretend we already initialized.
    telemetry._INITIALIZED = True
    with patch("app.core.telemetry.get_settings") as mock:
        # Even with an endpoint, a second call returns True without reinstalling.
        mock.return_value.otel_exporter_otlp_endpoint = "https://otlp.example"
        assert telemetry.configure_telemetry(app=MagicMock()) is True
    telemetry._INITIALIZED = False  # reset for other tests
