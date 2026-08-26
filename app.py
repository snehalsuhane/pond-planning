"""
Pond Planning System — Flask Application Entry Point
"""

import os
from flask import Flask
from flask_cors import CORS

from routes.contour import contour_bp


def create_app():
    app = Flask(__name__)

    # ── Configuration ──────────────────────────────────────────────────────────
    app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit
    app.config["ALLOWED_EXTENSIONS"] = {"kml", "kmz"}

    # Ensure uploads directory exists
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # ── CORS ───────────────────────────────────────────────────────────────────
    CORS(app)

    # ── Blueprints ─────────────────────────────────────────────────────────────
    app.register_blueprint(contour_bp, url_prefix="/api")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
