"""
Service: contour_service

Handles business logic for the /api/analyzeContour endpoint.

Current scope:
  - Validate file extension (KML / KMZ only)
  - Save file to the uploads directory
  - Parse KML/KMZ via kml_parser
  - Return basic parsing summary (status, filename, contour_count)

Future scope:
  - Run terrain analysis  (analysis/terrain.py)
  - Run catchment analysis (analysis/catchment.py)
"""

import os
from werkzeug.utils import secure_filename

from utils.kml_parser import parse, KMLParseError


def _allowed_extension(filename: str, allowed_extensions: set) -> bool:
    """Return True if the file has an allowed extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed_extensions
    )


def handle_contour_upload(file, upload_folder: str, allowed_extensions: set):
    """
    Validate, save, and parse a KML/KMZ upload.

    Parameters
    ----------
    file               : werkzeug FileStorage object from the request
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
            422,  # Unprocessable Entity
        )

    # ── 4. (Stub) Terrain analysis ──────────────────────────────────────────
    # from analysis.terrain import analyse as terrain_analyse
    # terrain_result = terrain_analyse(contours)

    # ── 5. (Stub) Catchment analysis ────────────────────────────────────────
    # from analysis.catchment import analyse as catchment_analyse
    # catchment_result = catchment_analyse(contours)

    # ── 6. Return parsing summary ────────────────────────────────────────────
    return (
        {
            "status": "success",
            "filename": filename,
            "contour_count": len(contours),
        },
        200,
    )
