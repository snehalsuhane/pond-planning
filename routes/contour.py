"""
Route: /api/analyzeContour and /api/analyzeArea

Accepts a multipart/form-data POST with a KML or KMZ file
under the field name ``contour_map``.

Progress polling
~~~~~~~~~~~~~~~~
``POST /api/analyzeArea`` returns a ``progress_token`` in the JSON response
and also in the ``X-Progress-Token`` response header immediately when the
analysis is queued.  Callers may poll::

    GET /api/analysis/status/<token>

which returns::

    {"stage": "Tracing drainage…", "done": false}
    {"stage": "done",              "done": true}
    {"stage": "error",             "done": true}
    {"stage": "unknown",           "done": true}   # token expired or never issued

This is a simple synchronous approach compatible with all WSGI servers; no
threading or streaming is required.
"""

from flask import Blueprint, request, current_app, jsonify

from services.contour_service import handle_contour_upload
from utils.land_selection import parse_land_area, LandSelectionError
from services.area_service import analyze_selected_land

from services.water_volume import add_water_volumes, parse_runoff_coefficient, DEFAULT_RUNOFF_COEFFICIENT

contour_bp = Blueprint("contour", __name__)


@contour_bp.get('/analysis/status/<token>')
def analysis_status(token):
    """Return the current pipeline stage for a running or completed analysis."""
    from services.progress import get_stage
    entry = get_stage(token)
    if entry is None:
        return jsonify({'stage': 'unknown', 'done': True}), 200
    stage = entry['stage']
    done = stage in ('done', 'error')
    return jsonify({'stage': stage, 'done': done}), 200


@contour_bp.post('/analyzeArea')
def analyze_area():
    """Analyze a required GeoJSON land_area using public elevation, without a file."""
    from services.progress import new_token
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'land_area' not in data:
        return jsonify({'status': 'error', 'error': 'Send a JSON object containing land_area (a GeoJSON Polygon).'}), 400
    try:
        land = parse_land_area(data['land_area'])
        coefficient = parse_runoff_coefficient(data.get('runoff_coefficient', DEFAULT_RUNOFF_COEFFICIENT))
    except ValueError as exc:
        return jsonify({'status': 'error', 'error': str(exc)}), 400

    token = new_token()
    result, code = analyze_selected_land(
        land, edge_setback_m=current_app.config['POND_EDGE_SETBACK_M'],
        water_buffer_m=current_app.config['WATERWAY_BUFFER_M'],
        max_slope_deg=current_app.config['POND_MAX_SLOPE_DEG'],
        progress_token=token)
    if code == 200:
        add_water_volumes(result, coefficient, land)
        result['progress_token'] = token
    response = jsonify(result)
    response.headers['X-Progress-Token'] = token
    return response, code


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

    try:
        coefficient = parse_runoff_coefficient(request.form.get('runoff_coefficient', DEFAULT_RUNOFF_COEFFICIENT))
    except ValueError as exc:
        return jsonify({'status': 'error', 'error': str(exc)}), 400

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

    if status_code == 200:
        add_water_volumes(result, coefficient, land_area)
    return jsonify(result), status_code
