"""
telemetry/setup.py
==================
OpenTelemetry bootstrap for the Village Pond Planning System.

Exporters configured
--------------------
* Traces  → Jaeger  (OTLP/HTTP on localhost:4318, internal)  +  Console (stdout)
* Metrics → Prometheus scrape endpoint on :6000  (external port 6273)
* Logs    → OTLP/HTTP  +  rotating JSON file  +  console

Call ``init_telemetry(app)`` once inside ``create_app()`` before any
requests are served.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

# ── Optional Jaeger exporter ────────────────────────────────────────────────
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPHTTPSpanExporter
    _OTLP_HTTP_AVAILABLE = True
except ImportError:
    _OTLP_HTTP_AVAILABLE = False

# ── Optional Prometheus exporter ────────────────────────────────────────────
try:
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    from prometheus_client import start_http_server as _prom_start
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False

# ── Service resource ─────────────────────────────────────────────────────────
RESOURCE = Resource.create({
    SERVICE_NAME: "village-pond-planning",
    SERVICE_VERSION: "1.0.0",
    "deployment.environment": os.getenv("FLASK_ENV", "development"),
})

# Public tracer / meter for manual instrumentation in services
tracer: trace.Tracer = None  # type: ignore[assignment]
meter: metrics.Meter = None  # type: ignore[assignment]

# Prometheus port — default 6000 maps to external port 6273 on
# machine 2273. Override via the PROMETHEUS_PORT environment variable.
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "6000"))

# OTLP endpoint (Jaeger all-in-one default)
OTLP_HTTP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")


def _build_tracer_provider() -> TracerProvider:
    """Configure and return a TracerProvider with Jaeger OTLP + console exporters."""
    provider = TracerProvider(resource=RESOURCE)

    # Always export to console so traces are visible even without Jaeger
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    # Export to Jaeger (via OTLP/HTTP) when available
    if _OTLP_HTTP_AVAILABLE:
        try:
            otlp_exporter = OTLPHTTPSpanExporter(
                endpoint=f"{OTLP_HTTP_ENDPOINT}/v1/traces",
            )
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logging.getLogger(__name__).info(
                "OTel: OTLP/HTTP trace exporter -> %s", OTLP_HTTP_ENDPOINT
            )
        except Exception as exc:  # pragma: no cover
            logging.getLogger(__name__).warning(
                "OTel: OTLP trace exporter not started: %s", exc
            )

    return provider


def _build_meter_provider() -> MeterProvider:
    """Configure and return a MeterProvider with Prometheus + console readers."""
    readers = [PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60_000)]

    if _PROMETHEUS_AVAILABLE:
        try:
            prom_reader = PrometheusMetricReader()
            readers.append(prom_reader)
            _prom_start(PROMETHEUS_PORT)
            logging.getLogger(__name__).info(
                "OTel: Prometheus metrics scrape endpoint -> http://localhost:%d/metrics",
                PROMETHEUS_PORT,
            )
        except Exception as exc:  # pragma: no cover
            logging.getLogger(__name__).warning(
                "OTel: Prometheus metric reader not started: %s", exc
            )

    return MeterProvider(resource=RESOURCE, metric_readers=readers)


def _configure_logging(log_file: str = "logs/otel_app.jsonl") -> None:
    """Attach a JSON-structured rotating file handler to the root logger."""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    class _JsonFormatter(logging.Formatter):
        import json as _json

        def format(self, record: logging.LogRecord) -> str:  # noqa: A003
            import json
            from opentelemetry import trace as _trace

            span = _trace.get_current_span()
            ctx = span.get_span_context() if span else None
            payload = {
                "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "trace_id": f"{ctx.trace_id:032x}" if ctx and ctx.is_valid else None,
                "span_id": f"{ctx.span_id:016x}" if ctx and ctx.is_valid else None,
                "module": record.module,
                "funcName": record.funcName,
                "lineno": record.lineno,
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload)

    file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(_JsonFormatter())
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(file_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(console_handler)


def _register_metrics(m: metrics.Meter) -> dict:
    """Create and return all application-level metric instruments."""
    return {
        # HTTP-layer counters
        "http_requests_total": m.create_counter(
            "http_requests_total",
            unit="1",
            description="Total HTTP requests received, labelled by route and status.",
        ),
        "http_errors_total": m.create_counter(
            "http_errors_total",
            unit="1",
            description="Total HTTP 4xx/5xx responses, labelled by route and status.",
        ),
        # Latency histogram (manual, per-endpoint)
        "request_duration_ms": m.create_histogram(
            "request_duration_ms",
            unit="ms",
            description="HTTP request processing duration in milliseconds.",
        ),
        # Domain-specific counters
        "contour_uploads_total": m.create_counter(
            "contour_uploads_total",
            unit="1",
            description="Total KML/KMZ contour files uploaded for analysis.",
        ),
        "pond_analyses_total": m.create_counter(
            "pond_analyses_total",
            unit="1",
            description="Total pond-site analysis runs completed.",
        ),
        "pond_candidates_found": m.create_histogram(
            "pond_candidates_found",
            unit="1",
            description="Number of viable pond candidates returned per analysis.",
        ),
        "elevation_fetches_total": m.create_counter(
            "elevation_fetches_total",
            unit="1",
            description="Total external elevation tile fetches (Copernicus GLO-30).",
        ),
        "elevation_cache_hits": m.create_counter(
            "elevation_cache_hits",
            unit="1",
            description="Elevation tile cache hits (disk cache).",
        ),
        "waterway_screens_total": m.create_counter(
            "waterway_screens_total",
            unit="1",
            description="Total OSM waterway screening calls.",
        ),
        # Gauge: active analyses in flight
        "active_analyses": m.create_up_down_counter(
            "active_analyses",
            unit="1",
            description="Number of analysis requests currently being processed.",
        ),
    }


# Module-level store so routes can import metrics
_app_metrics: dict = {}


def get_metrics() -> dict:
    """Return the registered metric instruments (populated after init_telemetry)."""
    return _app_metrics


def init_telemetry(app) -> None:
    """
    Bootstrap OpenTelemetry for the Flask application.

    Must be called once inside ``create_app()`` before any blueprint
    registration or before the first request is served.
    """
    global tracer, meter, _app_metrics

    # ---- Logging ------------------------------------------------------------
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "otel_app.jsonl")
    _configure_logging(log_path)
    log = logging.getLogger(__name__)
    log.info("OTel: Initialising OpenTelemetry for '%s'", RESOURCE.attributes[SERVICE_NAME])

    # ---- Tracing ------------------------------------------------------------
    tracer_provider = _build_tracer_provider()
    trace.set_tracer_provider(tracer_provider)
    tracer = trace.get_tracer("pond-planning", "1.0.0")

    # ---- Metrics ------------------------------------------------------------
    meter_provider = _build_meter_provider()
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter("pond-planning", "1.0.0")
    _app_metrics = _register_metrics(meter)

    # ---- Auto-instrumentation -----------------------------------------------
    FlaskInstrumentor().instrument_app(app)
    RequestsInstrumentor().instrument()

    log.info(
        "OTel: Initialisation complete - traces=%s, metrics=%s",
        "OTLP+console" if _OTLP_HTTP_AVAILABLE else "console",
        "Prometheus+console" if _PROMETHEUS_AVAILABLE else "console",
    )
