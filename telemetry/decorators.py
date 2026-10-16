"""
telemetry/decorators.py
=======================
Convenience decorators and context-manager helpers for manual spans and metrics.
Import these in service/route modules to add domain-level instrumentation.
"""

import functools
import logging
import time
from contextlib import contextmanager
from typing import Callable, Any, Generator

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from telemetry.setup import get_metrics

log = logging.getLogger(__name__)


@contextmanager
def span(name: str, attributes: dict | None = None) -> Generator:
    """
    Context manager that wraps a block in a named child span.

    Usage::

        with span("dem.generate", {"resolution_m": 30}):
            dem_result = generate_dem(projected_contours)
    """
    tracer = trace.get_tracer("pond-planning")
    with tracer.start_as_current_span(name) as s:
        if attributes:
            for k, v in attributes.items():
                if isinstance(v, (str, bool, int, float)):
                    s.set_attribute(k, v)
                else:
                    s.set_attribute(k, str(v))
        try:
            yield s
        except Exception as exc:
            s.set_status(Status(StatusCode.ERROR, str(exc)))
            s.record_exception(exc)
            raise


def traced(name: str | None = None, attributes: dict | None = None) -> Callable:
    """
    Decorator that wraps a function in a named OTel span.

    Usage::

        @traced("contour.parse")
        def parse(path):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        span_name = name or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Any:
            tracer = trace.get_tracer("pond-planning")
            with tracer.start_as_current_span(span_name) as s:
                if attributes:
                    for k, v in attributes.items():
                        if isinstance(v, (str, bool, int, float)):
                            s.set_attribute(k, v)
                        else:
                            s.set_attribute(k, str(v))
                try:
                    result = fn(*args, **kwargs)
                    s.set_status(Status(StatusCode.OK))
                    return result
                except Exception as exc:
                    s.set_status(Status(StatusCode.ERROR, str(exc)))
                    s.record_exception(exc)
                    raise
        return wrapper
    return decorator


def timed_metric(metric_key: str, labels: dict | None = None) -> Callable:
    """
    Decorator that records the wall-clock duration (ms) of a function call
    into the histogram identified by ``metric_key`` in the app metrics dict.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Any:
            app_metrics = get_metrics()
            hist = app_metrics.get(metric_key)
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                if hist is not None:
                    hist.record(elapsed_ms, attributes=labels or {})
        return wrapper
    return decorator
