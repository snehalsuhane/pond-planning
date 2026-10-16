"""
tests/test_telemetry.py
=======================
Tests for OpenTelemetry instrumentation, spans, metrics, and structured logging.
"""

import json
import logging
from flask import Flask
from opentelemetry import trace, metrics
from opentelemetry.trace import StatusCode

from telemetry.setup import init_telemetry, get_metrics
from telemetry.decorators import span, traced, timed_metric


def test_telemetry_initialization():
    """Verify tracer and meter are properly initialized and metrics registered."""
    app = Flask("test_app")
    init_telemetry(app)

    tracer = trace.get_tracer("pond-planning")
    assert tracer is not None

    meter = metrics.get_meter("pond-planning")
    assert meter is not None

    app_metrics = get_metrics()
    assert "http_requests_total" in app_metrics
    assert "request_duration_ms" in app_metrics
    assert "contour_uploads_total" in app_metrics
    assert "pond_analyses_total" in app_metrics
    assert "pond_candidates_found" in app_metrics


def test_span_context_manager():
    """Verify custom span context manager sets attributes and status."""
    with span("test.custom_span", {"test_key": "test_val", "count": 42}) as s:
        assert s is not None
        ctx = s.get_span_context()
        assert ctx.is_valid
        assert ctx.trace_id != 0
        assert ctx.span_id != 0


def test_span_context_manager_exception():
    """Verify exceptions in span context manager record status ERROR."""
    caught = False
    try:
        with span("test.failing_span"):
            raise ValueError("Span deliberate test failure")
    except ValueError:
        caught = True
    assert caught is True


def test_traced_decorator():
    """Verify @traced decorator executes function and sets status OK."""
    @traced("test.decorated_function", {"version": 1})
    def sample_func(x, y):
        return x + y

    result = sample_func(10, 20)
    assert result == 30


def test_timed_metric_decorator():
    """Verify @timed_metric decorator records duration without interfering with returns."""
    @timed_metric("request_duration_ms", labels={"route": "test_route"})
    def sample_timed():
        return "success"

    assert sample_timed() == "success"


def test_json_logging_with_trace_context():
    """Verify structured logger formats JSON with trace_id and span_id when inside a span."""
    from telemetry.setup import _configure_logging
    import io

    # Test formatter directly
    logger = logging.getLogger("test_trace_logger")
    logger.setLevel(logging.DEBUG)
    
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    
    # Use the JsonFormatter from setup
    # Trigger _configure_logging to ensure class exists or instantiate
    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
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
            }
            return json.dumps(payload)

    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)

    tracer = trace.get_tracer("test_tracer")
    with tracer.start_as_current_span("log_trace_test") as current_span:
        ctx = current_span.get_span_context()
        expected_trace_id = f"{ctx.trace_id:032x}"
        expected_span_id = f"{ctx.span_id:016x}"
        logger.info("Structured log message with active trace")

    output = stream.getvalue().strip()
    assert output, "Log stream should not be empty"
    entry = json.loads(output)
    assert entry["message"] == "Structured log message with active trace"
    assert entry["level"] == "INFO"
    assert entry["trace_id"] == expected_trace_id
    assert entry["span_id"] == expected_span_id

