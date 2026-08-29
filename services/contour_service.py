"""
Service: contour_service

Handles business logic for the /api/analyzeContour endpoint.

Pipeline:
  1. Validate file extension
  2. Save to uploads/
  3. Parse KML/KMZ       →  utils.kml_parser.parse()
  4. Validate & analyse  →  analysis.terrain.analyze_contours()
  5. Project coordinates →  utils.projection.project_contours()
  6. Generate DEM        →  analysis.dem.generate_dem()
  7. Calculate slope     →  analysis.terrain.calculate_slope()
  8. Find pond candidate →  analysis.pond.find_pond_candidate()
  9. Return structured JSON response
"""

import os
from werkzeug.utils import secure_filename

import numpy as np
from utils.kml_parser import parse, KMLParseError
from utils.projection import project_contours
from analysis.terrain import analyze_contours, TerrainValidationError, calculate_slope
from analysis.dem import generate_dem, DEMGenerationError
from analysis.pond import find_pond_candidate, PondCandidateError


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

    # ── 6. Generate DEM ─────────────────────────────────────────────────────
    try:
        dem_result = generate_dem(projected_contours)
    except DEMGenerationError as exc:
        return (
            {"status": "error", "error": str(exc), "filename": filename},
            422,
        )

    # Save DEM arrays to disk alongside the KML for downstream use
    dem_save_path = save_path.rsplit(".", 1)[0] + "_dem.npy"
    np.save(dem_save_path, dem_result["dem"])

    # ── 7. Slope ──────────────────────────────────────────────────────────────────
    slope_result = calculate_slope(dem_result)

    # ── 8. Pond candidate ─────────────────────────────────────────────────────────
    epsg = crs_info["epsg"]
    try:
        candidate = find_pond_candidate(dem_result, slope_result, epsg)
    except PondCandidateError as exc:
        return (
            {"status": "error", "error": str(exc), "filename": filename},
            422,
        )

    # ── 9. Success response ────────────────────────────────────────────────────────
    terrain["crs"] = crs_info
    return (
        {
            "status": "success",
            "filename": filename,
            "terrain": terrain,
            "dem": {
                "resolution_m":  dem_result["resolution_m"],
                "shape":         list(dem_result["shape"]),
                "nan_fraction":  dem_result["nan_fraction"],
                "elevation_min": dem_result["elevation_min"],
                "elevation_max": dem_result["elevation_max"],
                "bounds":        dem_result["bounds"],
                "saved_to":      os.path.basename(dem_save_path),
                "slope": {
                    "slope_min_deg":  slope_result["slope_min"],
                    "slope_max_deg":  slope_result["slope_max"],
                    "slope_mean_deg": slope_result["slope_mean"],
                },
            },
            "pond_site": candidate["pond_site"],
        },
        200,
    )
