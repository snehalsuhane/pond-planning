"""
Analysis: terrain

Validates parsed contour data and extracts terrain metadata.

Public API
----------
analyze_contours(contours) -> dict
    Accepts the list of contour dicts produced by utils.kml_parser.parse()
    and returns a terrain metadata dict, or raises TerrainValidationError.
"""

from statistics import median, mode
from collections import Counter


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class TerrainValidationError(ValueError):
    """
    Raised when the contour dataset fails validation and cannot be
    used for reliable terrain / DEM generation.
    """


# ---------------------------------------------------------------------------
# Internal validation helpers
# ---------------------------------------------------------------------------

_LON_RANGE = (-180.0, 180.0)
_LAT_RANGE = (-90.0,  90.0)
_MIN_POINTS_PER_CONTOUR = 2


def _validate_contours_present(contours: list):
    if not contours:
        raise TerrainValidationError("No contours supplied — list is empty.")


def _validate_single_contour(contour: dict, idx: int):
    """
    Validate a single contour dict for structural correctness.
    Raises TerrainValidationError with a descriptive message on failure.
    """
    # ── Elevation ────────────────────────────────────────────────────────────
    elevation = contour.get("elevation")
    if elevation is None:
        raise TerrainValidationError(
            f"Contour at index {idx} is missing an elevation value."
        )
    if not isinstance(elevation, (int, float)):
        raise TerrainValidationError(
            f"Contour at index {idx} has a non-numeric elevation: {elevation!r}."
        )

    # ── Coordinates presence ─────────────────────────────────────────────────
    coordinates = contour.get("coordinates")
    if not coordinates:
        raise TerrainValidationError(
            f"Contour at index {idx} (elevation={elevation}) has no coordinates."
        )

    if len(coordinates) < _MIN_POINTS_PER_CONTOUR:
        raise TerrainValidationError(
            f"Contour at index {idx} (elevation={elevation}) has only "
            f"{len(coordinates)} coordinate point(s); at least "
            f"{_MIN_POINTS_PER_CONTOUR} are required."
        )

    # ── Per-point range checks ───────────────────────────────────────────────
    for pt_idx, pt in enumerate(coordinates):
        if len(pt) < 2:
            raise TerrainValidationError(
                f"Contour {idx}, point {pt_idx}: expected [lon, lat], got {pt!r}."
            )
        lon, lat = pt[0], pt[1]
        if not (_LON_RANGE[0] <= lon <= _LON_RANGE[1]):
            raise TerrainValidationError(
                f"Contour {idx}, point {pt_idx}: longitude {lon} is outside "
                f"valid range [{_LON_RANGE[0]}, {_LON_RANGE[1]}]."
            )
        if not (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1]):
            raise TerrainValidationError(
                f"Contour {idx}, point {pt_idx}: latitude {lat} is outside "
                f"valid range [{_LAT_RANGE[0]}, {_LAT_RANGE[1]}]."
            )


# ---------------------------------------------------------------------------
# Contour interval logic
# ---------------------------------------------------------------------------

_TOLERANCE = 1e-6   # floating-point comparison tolerance (metres)


def _round_interval(value: float, decimals: int = 4) -> float:
    """Round a floating-point interval to remove float noise."""
    return round(value, decimals)


def _compute_interval(unique_sorted_elevations: list) -> tuple:
    """
    Derive the contour interval from the sorted list of unique elevations.

    Returns
    -------
    (interval_m, uniform)
        interval_m : float — the dominant/representative interval
        uniform    : bool  — True only when every gap equals interval_m
    """
    if len(unique_sorted_elevations) < 2:
        # Only one elevation level — cannot determine an interval
        return (None, False)

    gaps = [
        _round_interval(unique_sorted_elevations[i + 1] - unique_sorted_elevations[i])
        for i in range(len(unique_sorted_elevations) - 1)
    ]

    # Choose the most frequent gap as the representative interval
    gap_counts = Counter(gaps)
    dominant_gap = gap_counts.most_common(1)[0][0]

    uniform = all(abs(g - dominant_gap) < _TOLERANCE for g in gaps)

    return (dominant_gap, uniform)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_contours(contours: list) -> dict:
    """
    Validate and analyse a list of contour dicts from kml_parser.parse().

    Parameters
    ----------
    contours : list[dict]
        Each dict must have keys: id, elevation, coordinates.

    Returns
    -------
    dict:
        {
            "contour_count"          : int,
            "min_elevation_m"        : float,
            "max_elevation_m"        : float,
            "contour_interval_m"     : float | None,
            "contour_interval_uniform": bool,
            "total_points"           : int,
            "bounds": {
                "min_lon": float,
                "min_lat": float,
                "max_lon": float,
                "max_lat": float
            }
        }

    Raises
    ------
    TerrainValidationError
        If the dataset is missing, structurally invalid, or contains
        coordinates outside legal geographic ranges.
    """
    # ── 1. Dataset-level check ───────────────────────────────────────────────
    _validate_contours_present(contours)

    # ── 2. Per-contour validation ────────────────────────────────────────────
    for idx, contour in enumerate(contours):
        _validate_single_contour(contour, idx)

    # ── 3. Aggregate statistics ──────────────────────────────────────────────
    all_elevations = [c["elevation"] for c in contours]
    unique_sorted_elevs = sorted(set(all_elevations))

    if len(unique_sorted_elevs) < 2:
        raise TerrainValidationError(
            f"Insufficient elevation data: only {len(unique_sorted_elevs)} unique "
            f"elevation level(s) found. At least 2 are required to derive a "
            f"contour interval and perform terrain analysis."
        )

    total_points = sum(len(c["coordinates"]) for c in contours)

    # Flatten all coordinates for bounds calculation
    all_lons = [pt[0] for c in contours for pt in c["coordinates"]]
    all_lats = [pt[1] for c in contours for pt in c["coordinates"]]

    interval_m, uniform = _compute_interval(unique_sorted_elevs)

    return {
        "contour_count":            len(contours),
        "min_elevation_m":          min(all_elevations),
        "max_elevation_m":          max(all_elevations),
        "contour_interval_m":       interval_m,
        "contour_interval_uniform": uniform,
        "total_points":             total_points,
        "bounds": {
            "min_lon": min(all_lons),
            "min_lat": min(all_lats),
            "max_lon": max(all_lons),
            "max_lat": max(all_lats),
        },
    }
