"""
Service: contour_service

Handles business logic for the /api/analyzeContour endpoint.

Current scope:
  - Validate file extension (KML / KMZ only)
  - Return the filename

Future scope (stubs included):
  - Save file to disk
  - Parse KML/KMZ via kml_parser
  - Run terrain analysis  (analysis/terrain.py)
  - Run catchment analysis (analysis/catchment.py)
"""

import os
from werkzeug.utils import secure_filename


def _allowed_extension(filename: str, allowed_extensions: set) -> bool:
    """Return True if the file has an allowed extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed_extensions
    )


def handle_contour_upload(file, upload_folder: str, allowed_extensions: set):
    """
    Validate and (optionally) save a KML/KMZ upload.

    Parameters
    ----------
    file              : werkzeug FileStorage object from the request
    upload_folder     : absolute path to the uploads directory
    allowed_extensions: set of permitted extensions e.g. {'kml', 'kmz'}

    Returns
    -------
    (dict, int)  — JSON-serialisable response body and HTTP status code
    """
    filename = secure_filename(file.filename)

    # ── Extension validation ────────────────────────────────────────────────
    if not _allowed_extension(filename, allowed_extensions):
        return (
            {
                "success": False,
                "error": (
                    f"Invalid file type '{filename.rsplit('.', 1)[-1]}'. "
                    f"Allowed types: {', '.join(sorted(allowed_extensions))}."
                ),
            },
            415,  # Unsupported Media Type
        )

    # ── (Stub) Save file ────────────────────────────────────────────────────
    # save_path = os.path.join(upload_folder, filename)
    # file.save(save_path)

    # ── (Stub) Parse KML/KMZ ───────────────────────────────────────────────
    # from utils.kml_parser import parse
    # geodata = parse(save_path)

    # ── (Stub) Terrain analysis ─────────────────────────────────────────────
    # from analysis.terrain import analyse
    # terrain_result = analyse(geodata)

    # ── (Stub) Catchment analysis ───────────────────────────────────────────
    # from analysis.catchment import analyse
    # catchment_result = analyse(geodata)

    # ── Current response: echo validated filename ───────────────────────────
    return (
        {
            "success": True,
            "message": "File validated successfully.",
            "filename": filename,
        },
        200,
    )
