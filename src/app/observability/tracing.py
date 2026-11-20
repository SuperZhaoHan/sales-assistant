from __future__ import annotations

from typing import Any

import structlog

from app.core.config import AppConfig

logger = structlog.get_logger(__name__)


def setup_tracing(settings: AppConfig, app: Any | None = None) -> None:
    """Best-effort OpenTelemetry setup.

    Tracing is optional. If the ``otel`` extra is not installed, a warning is
    logged and the service continues normally.
    """

    if not settings.observability.otel_enabled:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("otel_enabled_but_dependencies_missing")
        return

    resource = Resource.create(
        {
            "service.name": settings.app.name,
            "deployment.environment": settings.env,
            "service.version": settings.app.version,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = (
        OTLPSpanExporter(endpoint=settings.observability.otel_endpoint)
        if settings.observability.otel_endpoint
        else OTLPSpanExporter()
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    logger.info("otel_tracing_enabled", endpoint=settings.observability.otel_endpoint)
