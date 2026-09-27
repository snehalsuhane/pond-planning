"""
Route: /api/analyzeContour

Accepts a multipart/form-data POST with a KML or KMZ file
under the field name ``contour_map``.
"""

from flask import Blueprint, request, current_app, jsonify

from services.contour_service import handle_contour_upload
from utils.land_selection import parse_land_area, LandSelectionError

contour_bp = Blueprint("contour", __name__)


@contour_bp.route("/analyzeContour", methods=["POST"])
def analyze_contour():
    """
    POST /api/analyzeContour
    ---
    Consumes:
      - multipart/form-data (field: contour_map)
    Produces:
      - application/json
    """
    # ── 1. Check file field is present ─────────────────────────────────────
    if "contour_map" not in request.files:
        return jsonify({"success": False, "error": "No 'contour_map' file field in request."}), 400

    file = request.files["contour_map"]

    if file.filename == "":
        return jsonify({"success": False, "error": "No file selected."}), 400

    # Optional land selection leaves existing upload-only clients unchanged.
    land_area = None
    if 'land_area' in request.form:
        try:
            land_area = parse_land_area(request.form['land_area'])
        except LandSelectionError as exc:
            return jsonify({'status': 'error', 'error': str(exc)}), 400

    # ── 2. Delegate to service layer ────────────────────────────────────────
    result, status_code = handle_contour_upload(
        file=file,
        upload_folder=current_app.config["UPLOAD_FOLDER"],
        allowed_extensions=current_app.config["ALLOWED_EXTENSIONS"],
        edge_setback_m=current_app.config["POND_EDGE_SETBACK_M"],
        water_buffer_m=current_app.config["WATERWAY_BUFFER_M"],
        max_slope_deg=current_app.config['POND_MAX_SLOPE_DEG'],
        land_area=land_area,
    )

    return jsonify(result), status_code
