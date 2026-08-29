"""
Service: contour_service

Handles business logic for the /api/analyzeContour endpoint.

Pipeline:
  1. Validate file extension
  2. Save to uploads/
  3. Parse KML/KMZ       →  utils.kml_parser.parse()
  4. Validate & analyse  →  analysis.terrain.analyze_contours()
  5. Project coordinates →  utils.projection.project_contours()
  6. Return structured JSON response
"""

import os
from werkzeug.utils import secure_filename

from utils.kml_parser import parse, KMLParseError
from utils.projection import project_contours
from analysis.terrain import analyze_contours, TerrainValidationError


def _allowed_extension(filename: str, allowed_extensions: set) -> bool:
    """Return True if the file has an allowed extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed_extensions
    )


def handle_contour_upload(file, upload_folder: str, allowed_extensions: set):
    """
    Validate, save, parse, and analyse a KML/KMZ upload.

    Parameters
    ----------
    file               : werkzeug FileStorage object
    upload_folder      : absolute path to the uploads directory
    allowed_extensions : set of permitted extensions, e.g. {'kml', 'kmz'}

    Returns
    -------
    (dict, int)  — JSON-serialisable response body and HTTP status code
    """
    filename = secure_filename(file.filename)

    # ── 1. Extension validation ─────────────────────────────────────────────
    if not _allowed_extension(filename, allowed_extensions):
        ext = filename.rsplit(".", 1)[-1] if "." in filename else filename
        return (
            {
                "status": "error",
                "error": (
                    f"Invalid file type '.{ext}'. "
                    f"Allowed: {', '.join(sorted(allowed_extensions))}."
                ),
            },
            415,
        )

    # ── 2. Save to disk ─────────────────────────────────────────────────────
    save_path = os.path.join(upload_folder, filename)
    file.save(save_path)

    # ── 3. Parse KML/KMZ ────────────────────────────────────────────────────
    try:
        contours = parse(save_path)
    except KMLParseError as exc:
        return (
            {
                "status": "error",
                "error": str(exc),
                "filename": filename,
            },
            422,
        )

    # ── 4. Terrain validation & analysis ────────────────────────────────────
    try:
        terrain = analyze_contours(contours)
    except TerrainValidationError as exc:
        return (
            {
                "status": "error",
                "error": str(exc),
                "filename": filename,
            },
            422,
        )

    # ── 5. Project coordinates into metres ──────────────────────────────────
    projected_contours, crs_info = project_contours(contours)
    # projected_contours hold 'projected_coordinates' (X/Y metres) alongside
    # the original 'coordinates' (lon/lat).  They are not returned in the API
    # response (too large) but will be passed to DEM generation in a later step.

    # ── 6. Success response ──────────────────────────────────────────────────
    terrain["crs"] = crs_info   # embed the chosen CRS in the terrain block
    return (
        {
            "status": "success",
            "filename": filename,
            "terrain": terrain,
        },
        200,
    )
