"""
Pond Planning System — Flask Application Entry Point
"""

import os
import time
import logging
from flask import Flask, render_template, request, g
from flask_cors import CORS

from routes.contour import contour_bp
from routes.places import places_bp
from routes.pond_design import pond_design_bp

log = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    # ── Configuration ──────────────────────────────────────────────────────────
    app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit
    app.config["ALLOWED_EXTENSIONS"] = {"kml", "kmz"}
    app.config["POND_EDGE_SETBACK_M"] = 100.0
    app.config["WATERWAY_BUFFER_M"] = 30.0
    app.config["POND_MAX_SLOPE_DEG"] = 8.0

    # Ensure uploads directory exists
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # ── OpenTelemetry (must come before blueprints) ────────────────────────────
    from telemetry.setup import init_telemetry
    init_telemetry(app)

    # ── CORS ───────────────────────────────────────────────────────────────────
    CORS(app)

    # ── Blueprints ─────────────────────────────────────────────────────────────
    app.register_blueprint(contour_bp, url_prefix="/api")
    app.register_blueprint(places_bp, url_prefix="/api")
    app.register_blueprint(pond_design_bp, url_prefix="/api")

    # ── OTel request-level metrics middleware ──────────────────────────────────
    from telemetry.setup import get_metrics

    @app.before_request
    def _before():
        g._otel_start = time.perf_counter()

    @app.after_request
    def _after(response):
        app_metrics = get_metrics()
        elapsed_ms = (time.perf_counter() - getattr(g, "_otel_start", time.perf_counter())) * 1000.0
        route = request.endpoint or "unknown"
        status = str(response.status_code)

        attrs = {"route": route, "method": request.method, "status_code": status}

        ctr = app_metrics.get("http_requests_total")
        if ctr:
            ctr.add(1, attributes=attrs)

        hist = app_metrics.get("request_duration_ms")
        if hist:
            hist.record(elapsed_ms, attributes=attrs)

        if response.status_code >= 400:
            err_ctr = app_metrics.get("http_errors_total")
            if err_ctr:
                err_ctr.add(1, attributes=attrs)

        log.debug(
            "Request completed: %s %s -> %s (%.1f ms)",
            request.method, request.path, response.status_code, elapsed_ms,
        )
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    return app


if __name__ == "__main__":
    import os as _os
    _debug = _os.getenv("FLASK_ENV", "production") == "development"
    app = create_app()
    # use_reloader=False prevents the Prometheus port from binding twice
    # when Flask's stat-reloader forks the process.
    app.run(debug=_debug, use_reloader=False, host="0.0.0.0", port=5000)
