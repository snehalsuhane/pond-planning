"""
Service: contour_service

Pipeline:
  1. Validate extension
  2. Save to uploads/
  3. Parse KML/KMZ       →  utils.kml_parser.parse()
  4. Validate & analyse  →  analysis.terrain.analyze_contours()
  5. Project coordinates →  utils.projection.project_contours()
  6. Generate DEM        →  analysis.dem.generate_dem()
  7. Calculate slope     →  analysis.terrain.calculate_slope()
  8. Run hydrology       →  analysis.hydrology.run_hydrology()
  9. Rank pond candidates→  analysis.pond.rank_pond_candidates()
 10. Return JSON response
"""

import os
import logging
from werkzeug.utils import secure_filename

import numpy as np
from utils.kml_parser import parse, KMLParseError
from utils.projection import project_contours
from analysis.terrain import analyze_contours, TerrainValidationError, calculate_slope
from analysis.dem import generate_dem, DEMGenerationError
from analysis.pond import rank_pond_candidates, PondCandidateError
from analysis.hydrology import run_hydrology
from services.waterways import screen_waterways, WaterwayDataError
from utils.land_selection import land_candidate_mask, LandSelectionError

# OpenTelemetry
try:
    from telemetry.decorators import span as otel_span
    from telemetry.setup import get_metrics
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    from contextlib import contextmanager
    @contextmanager
    def otel_span(name, attributes=None):  # type: ignore[misc]
        yield None
    def get_metrics(): return {}


def _allowed_extension(filename: str, allowed_extensions: set) -> bool:
    """Return True if the file has an allowed extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed_extensions
    )


def handle_contour_upload(file, upload_folder: str, allowed_extensions: set,
                          edge_setback_m: float = 100.0, water_buffer_m: float = 30.0,
                          max_slope_deg: float = 8.0, land_area=None):
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
    _log = logging.getLogger(__name__)
    filename = secure_filename(file.filename)
    # OTel: count every contour upload attempt
    app_metrics = get_metrics()
    ctr = app_metrics.get("contour_uploads_total")
    if ctr:
        ctr.add(1, attributes={"extension": filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"})
    _log.info("handle_contour_upload: received file '%s'", filename)

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
    with otel_span("contour.parse_kml", {"file.name": filename, "file.path": save_path}):
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
    with otel_span("contour.analyze_terrain", {"file.name": filename}):
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
    with otel_span("contour.project_coordinates"):
        projected_contours, crs_info = project_contours(contours)
    _log.info("contour.project: EPSG=%s", crs_info.get('epsg'))

    # ── 6. Generate DEM ─────────────────────────────────────────────────────
    with otel_span("contour.generate_dem", {"epsg": str(crs_info.get('epsg', ''))}):
        try:
            dem_result = generate_dem(projected_contours)
        except DEMGenerationError as exc:
            return (
                {"status": "error", "error": str(exc), "filename": filename},
                422,
            )

    # Keep the full survey for upstream routing, even outside the selected land.
    candidate_mask, land_metadata = None, None
    if land_area is not None:
        with otel_span("contour.land_candidate_mask"):
            try:
                candidate_mask, land_metadata = land_candidate_mask(land_area, dem_result, crs_info['epsg'])
            except LandSelectionError as exc:
                return {'status': 'error', 'error': str(exc), 'filename': filename}, 422

    # Save DEM arrays to disk alongside the KML for downstream use
    dem_save_path = save_path.rsplit(".", 1)[0] + "_dem.npy"
    np.save(dem_save_path, dem_result["dem"])

    # ── 7. Slope ──────────────────────────────────────────────────────────────────
    with otel_span("contour.calculate_slope", {
        "dem.resolution_m": dem_result.get("resolution_m", 0),
        "dem.shape": str(dem_result.get("shape", [])),
    }):
        slope_result = calculate_slope(dem_result)
    _log.info(
        "contour.slope: min=%.2f deg, max=%.2f deg, mean=%.2f deg",
        slope_result["slope_min"], slope_result["slope_max"], slope_result["slope_mean"],
    )

    # ── 8. Hydrology ─────────────────────────────────────────────────────────
    with otel_span("contour.run_hydrology"):
        hydro = run_hydrology(dem_result)
    _log.info(
        "contour.hydrology: acc_max=%s, channel_fraction=%.4f",
        hydro["acc_max"], hydro["channel_fraction"],
    )

    # ── 9. Pond candidates ───────────────────────────────────────────────────
    epsg = crs_info["epsg"]
    with otel_span("contour.screen_waterways", {"epsg": str(epsg), "buffer_m": water_buffer_m}):
        ww_ctr = app_metrics.get("waterway_screens_total")
        if ww_ctr:
            ww_ctr.add(1, attributes={"source": "osm"})
        try:
            water_mask, water_metadata = screen_waterways(dem_result, epsg, buffer_m=water_buffer_m)
        except WaterwayDataError as exc:
            _log.warning('Water screening unavailable: %s', exc)
            return {"status": "error",
                    "error": "Could not check for existing rivers and water bodies. Water-map data is unavailable. Check the server's internet connection and retry. No pond sites were selected without this check.",
                    "error_code": "waterway_screening_unavailable",
                    "waterway_screening": {"status": "unavailable"}}, 503
    with otel_span("contour.rank_pond_candidates", {
        "edge_setback_m": edge_setback_m,
        "max_slope_deg": max_slope_deg,
    }):
        try:
            candidates = rank_pond_candidates(
                dem_result, slope_result, epsg, hydrology=hydro,
                exclusion_mask=water_mask, edge_setback_m=edge_setback_m,
                candidate_mask=candidate_mask,
                max_slope_deg=max_slope_deg,
            )
        except PondCandidateError as exc:
            return (
                {"status": "error", "error": str(exc), "filename": filename},
                422,
            )

    # ── 10. Success response ──────────────────────────────────────────────────
    # Strip internal raster indices (grid_row / grid_col) before serialising —
    # callers only need geographic coordinates, not pixel positions.
    _INTERNAL = {"grid_row", "grid_col"}
    response_candidates = [
        {k: v for k, v in c.items() if k not in _INTERNAL}
        for c in candidates.get("pond_candidates", [])
    ]

    # OTel: record domain metrics for the completed analysis
    n_candidates = len(response_candidates)
    _log.info("handle_contour_upload: analysis complete, %d pond candidates found", n_candidates)
    pond_ctr = app_metrics.get("pond_analyses_total")
    if pond_ctr:
        pond_ctr.add(1, attributes={"source": "kml_upload", "status": "success"})
    cand_hist = app_metrics.get("pond_candidates_found")
    if cand_hist:
        cand_hist.record(n_candidates, attributes={"source": "kml_upload"})

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
                "extrapolated_fraction": dem_result["extrapolated_fraction"],
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
            "pond_candidates": response_candidates,
            "land_selection": land_metadata,
            "waterway_screening": water_metadata,
            "planning": {"edge_setback_m": edge_setback_m,
                         "site_scope": "selected_land" if land_metadata else "full_survey",
                         "terrain_scope": "full_uploaded_survey",
                         "catchments_clipped_to_land": False,
                         "assessment": "preliminary terrain screening",
                         "drainage_saturation_ha": candidates["drainage_saturation_ha"],
                         "outlet_snap_distance_m": 0.0, "catchment_display_unit": "ha",
                         "catchment_model": "connected upstream drainage to a collection target",
                         "ranking_version": "collection_targets_v3",
                         "collection_screening": candidates["screening"],
                         "drainage_normalization": "log1p catchment cells / log1p largest eligible catchment cells"},
            "hydrology": {
                "noflow_count":       hydro["noflow_count"],
                "acc_max":            hydro["acc_max"],
                "acc_mean":           round(hydro["acc_mean"], 2),
                "channel_threshold":  hydro["channel_threshold"],
                "channel_cell_count": hydro["channel_cell_count"],
                "channel_fraction":   round(hydro["channel_fraction"], 4),
            },
        },
        200,
    )
