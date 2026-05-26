"""OpenTelemetry initialisation.

This module is **opt-in**. EverCore ships without OpenTelemetry runtime
dependencies; install the ``otel`` dependency group to enable it:

    uv sync --group otel

At import time we soft-import the OTel SDK. If the SDK is missing, every
public function in this module degrades to a no-op so application startup
is not affected.

Activation is further gated on the ``OTEL_EXPORTER_OTLP_ENDPOINT``
environment variable. When that variable is unset, ``init_tracing`` is a
no-op even if the SDK is installed. This means a developer can install
the ``otel`` group without immediately exporting spans — useful for tests.

Configuration (read from environment):

- ``OTEL_EXPORTER_OTLP_ENDPOINT`` — collector endpoint (e.g.
  ``http://jaeger:4317``). Required to enable tracing.
- ``OTEL_SERVICE_NAME`` — service name reported in spans. Defaults to
  ``evercore``.
- ``OTEL_TRACES_SAMPLER_ARG`` — sampling ratio, 0.0–1.0. Defaults to
  0.1 (10%) to keep collector load bounded.
- ``OTEL_EXPORTER_OTLP_PROTOCOL`` — ``grpc`` (default) or ``http/protobuf``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-import OpenTelemetry. Anything imported under ``_otel_*`` is either a
# real OTel object or ``None``. Callers check the flags below before using.
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import Resource as _OtelResource
    from opentelemetry.sdk.trace import TracerProvider as _OtelTracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor as _OtelBatchSpanProcessor,
    )
    from opentelemetry.sdk.trace.sampling import (
        ParentBased as _OtelParentBased,
        TraceIdRatioBased as _OtelTraceIdRatioBased,
    )

    _OTEL_AVAILABLE = True
except ImportError:
    _otel_trace = None  # type: ignore[assignment]
    _OtelResource = None  # type: ignore[assignment]
    _OtelTracerProvider = None  # type: ignore[assignment]
    _OtelBatchSpanProcessor = None  # type: ignore[assignment]
    _OtelParentBased = None  # type: ignore[assignment]
    _OtelTraceIdRatioBased = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False


# Process-wide flag set after ``init_tracing`` succeeds. ``trace_logger``
# checks this to decide whether to open spans.
_TRACING_ACTIVE: bool = False


def is_tracing_active() -> bool:
    """Return True iff OTel was initialised and is exporting spans."""
    return _TRACING_ACTIVE


def init_tracing(app: Optional[Any] = None) -> bool:
    """Initialise OpenTelemetry tracing if the environment requests it.

    Safe to call multiple times — subsequent calls are no-ops.

    Args:
        app: Optional FastAPI application. When passed and the
            ``opentelemetry-instrumentation-fastapi`` package is
            available, the app is instrumented for incoming-request
            spans.

    Returns:
        True if tracing was activated, False otherwise. Callers can
        use this to log or surface the state at startup.
    """
    global _TRACING_ACTIVE

    if _TRACING_ACTIVE:
        return True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.debug(
            "OpenTelemetry disabled: OTEL_EXPORTER_OTLP_ENDPOINT not set"
        )
        return False

    if not _OTEL_AVAILABLE:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but opentelemetry SDK is "
            "not installed. Install the 'otel' dependency group "
            "(`uv sync --group otel`) to enable tracing."
        )
        return False

    service_name = os.environ.get("OTEL_SERVICE_NAME", "evercore")
    sample_ratio = _read_float_env("OTEL_TRACES_SAMPLER_ARG", default=0.1)

    resource = _OtelResource.create({"service.name": service_name})
    sampler = _OtelParentBased(root=_OtelTraceIdRatioBased(sample_ratio))
    provider = _OtelTracerProvider(resource=resource, sampler=sampler)

    exporter = _build_otlp_exporter(endpoint)
    if exporter is None:
        return False

    provider.add_span_processor(_OtelBatchSpanProcessor(exporter))
    _otel_trace.set_tracer_provider(provider)

    _install_optional_instrumentations(app)

    _TRACING_ACTIVE = True
    logger.info(
        "OpenTelemetry tracing active: endpoint=%s service=%s sample_ratio=%s",
        endpoint,
        service_name,
        sample_ratio,
    )
    return True


def get_tracer(name: str):
    """Return an OTel tracer, or None if tracing is inactive.

    The trace_logger decorator uses this to decide whether to open a span.
    """
    if not _TRACING_ACTIVE or _otel_trace is None:
        return None
    return _otel_trace.get_tracer(name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using default %s", name, raw, default)
        return default
    if not 0.0 <= value <= 1.0:
        logger.warning("%s=%s outside [0,1]; clamping", name, value)
        return max(0.0, min(1.0, value))
    return value


def _build_otlp_exporter(endpoint: str):
    """Build an OTLP exporter, preferring gRPC and falling back to HTTP."""
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower()

    if protocol in ("grpc", ""):
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            return OTLPSpanExporter(endpoint=endpoint, insecure=True)
        except ImportError:
            logger.warning(
                "OTLP gRPC exporter not installed; trying HTTP exporter"
            )

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=endpoint)
    except ImportError:
        logger.error(
            "No OTLP exporter available; install opentelemetry-exporter-otlp"
        )
        return None


def _install_optional_instrumentations(app: Optional[Any]) -> None:
    """Install any auto-instrumentations whose packages are present.

    Each instrumentation is wrapped in its own try/except so missing
    packages do not prevent the rest from loading.
    """
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
        except ImportError:
            logger.debug("FastAPI instrumentation not installed; skipping")

    for module_path, instrumentor_attr in (
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.pymongo", "PymongoInstrumentor"),
    ):
        try:
            module = __import__(module_path, fromlist=[instrumentor_attr])
            getattr(module, instrumentor_attr)().instrument()
        except ImportError:
            logger.debug("%s not installed; skipping", module_path)
        except Exception as exc:  # pragma: no cover — instrumentor-side failure  # noqa: BLE001
            logger.warning(
                "Failed to enable %s instrumentation: %s", module_path, exc
            )
