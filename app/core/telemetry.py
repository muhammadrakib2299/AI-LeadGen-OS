"""OpenTelemetry wiring — optional, no-op when OTEL_EXPORTER_OTLP_ENDPOINT unset.

Design goals:
- Zero config overhead when OTel isn't configured. Nothing imports OTel
  SDKs or starts background exporters unless an endpoint is set.
- Auto-instrument FastAPI, httpx, and SQLAlchemy so per-request traces
  fan out naturally: HTTP span → any DB + outbound API calls it made.
- Grafana Cloud is the target backend (todo.md §Observability) but any
  OTLP receiver works; the SDK reads standard env vars for auth.

Call `configure_telemetry(app, engine)` once at app startup, after the
FastAPI app exists but before it starts serving requests.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Guard against double-init (imports in tests, multi-worker setups).
_INITIALIZED = False


def configure_telemetry(app: Any, engine: Any | None = None) -> bool:
    """Set up OTel traces if an OTLP endpoint is configured.

    Returns True when instrumentation was installed, False when it was
    skipped. Safe to call repeatedly — subsequent calls are no-ops.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return True

    settings = get_settings()
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        log.info("telemetry_skipped", reason="no_otlp_endpoint")
        return False

    # Import lazily so the deps load only when needed. Keeps the no-telemetry
    # path feather-light and avoids tripping import-time side effects in envs
    # that don't care about observability.
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {"service.name": _service_name_from_env() or "ai-leadgen-os"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    if engine is not None:
        # SQLAlchemy instrumentor wants the concrete Engine/AsyncEngine.sync_engine
        sync_engine = getattr(engine, "sync_engine", engine)
        SQLAlchemyInstrumentor().instrument(engine=sync_engine)

    _INITIALIZED = True
    log.info("telemetry_configured", endpoint=endpoint)
    return True


def _service_name_from_env() -> str | None:
    import os

    return os.environ.get("OTEL_SERVICE_NAME")
