"""
Analysis: dem

Generates a Digital Elevation Model (DEM) from projected contour data.

Pipeline:
    projected contour lines
          ↓
    extract (X, Y, Z) scatter points from all line vertices
          ↓
    linear interpolation onto a regular grid (scipy.griddata)
          ↓
    nearest-neighbour fill for edge regions outside convex hull
          ↓
    regular elevation grid  →  slope / flow / catchment
"""

import numpy as np
from scipy.interpolate import griddata


class DEMGenerationError(ValueError):
    """Raised when DEM generation cannot proceed."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_scatter(projected_contours: list) -> tuple:
    """Return flat (xs, ys, zs) numpy arrays from all projected contour vertices."""
    xs, ys, zs = [], [], []
    for c in projected_contours:
        z = c["elevation"]
        for x, y in c["projected_coordinates"]:
            xs.append(x)
            ys.append(y)
            zs.append(z)
    return (
        np.array(xs, dtype=float),
        np.array(ys, dtype=float),
        np.array(zs, dtype=float),
    )


def _auto_resolution(min_x: float, max_x: float,
                     min_y: float, max_y: float,
                     contour_interval: float | None = None) -> float:
    """
    Choose a grid resolution targeting ~500 cells along the longest axis.
    Clamped to [1 m, 50 m] and optionally snapped to a multiple of the
    contour interval for clean grid alignment.
    """
    extent = max(max_x - min_x, max_y - min_y)
    if extent <= 0:
        return 1.0

    res = max(1.0, min(50.0, extent / 500.0))

    if contour_interval and 0 < contour_interval < res:
        n = max(1, round(res / contour_interval))
        res = float(n * contour_interval)

    return round(res, 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_dem(projected_contours: list, resolution: float | None = None) -> dict:
    """
    Build a DEM from projected contour lines.

    Parameters
    ----------
    projected_contours : list[dict]
        Contour dicts with 'projected_coordinates' (from projection.project_contours)
        and 'elevation'.
    resolution : float | None
        Grid cell size in metres.  Auto-derived when None.

    Returns
    -------
    dict:
        dem           - np.ndarray (rows, cols)  elevation values
        x_coords      - np.ndarray (cols,)       X axis positions (metres)
        y_coords      - np.ndarray (rows,)       Y axis positions (metres)
        resolution_m  - float                    grid spacing (metres)
        shape         - (int, int)               (rows, cols)
        bounds        - dict {min_x, min_y, max_x, max_y}
        nan_fraction  - float                    fraction of remaining NaN cells
        elevation_min - float
        elevation_max - float

    Raises
    ------
    DEMGenerationError
    """
    if not projected_contours:
        raise DEMGenerationError("No projected contours supplied.")

    for i, c in enumerate(projected_contours):
        if "projected_coordinates" not in c or not c["projected_coordinates"]:
            raise DEMGenerationError(
                f"Contour {i} (elevation={c.get('elevation')}) is missing "
                "'projected_coordinates'. Run projection.project_contours() first."
            )

    # ── Scatter points ───────────────────────────────────────────────────────
    xs, ys, zs = _extract_scatter(projected_contours)
    if len(xs) < 4:
        raise DEMGenerationError(
            f"Need ≥ 4 scatter points for interpolation, got {len(xs)}."
        )

    min_x, max_x = float(xs.min()), float(xs.max())
    min_y, max_y = float(ys.min()), float(ys.max())

    # ── Resolution ───────────────────────────────────────────────────────────
    if resolution is None:
        elevs = sorted({c["elevation"] for c in projected_contours})
        ci = None
        if len(elevs) >= 2:
            ci = min(b - a for a, b in zip(elevs, elevs[1:]))
        resolution = _auto_resolution(min_x, max_x, min_y, max_y, ci)

    # ── Regular grid ─────────────────────────────────────────────────────────
    grid_x = np.arange(min_x, max_x + resolution, resolution)
    grid_y = np.arange(min_y, max_y + resolution, resolution)
    gxx, gyy = np.meshgrid(grid_x, grid_y)

    # ── Interpolate ──────────────────────────────────────────────────────────
    pts = np.column_stack([xs, ys])
    dem = griddata(pts, zs, (gxx, gyy), method="linear")

    # Fill edge NaNs (outside convex hull) with nearest-neighbour
    nan_mask = np.isnan(dem)
    if nan_mask.any():
        dem_nn = griddata(pts, zs, (gxx, gyy), method="nearest")
        dem[nan_mask] = dem_nn[nan_mask]

    return {
        "dem":           dem,
        "x_coords":      grid_x,
        "y_coords":      grid_y,
        "resolution_m":  resolution,
        "shape":         dem.shape,
        "bounds":        {"min_x": min_x, "min_y": min_y,
                          "max_x": max_x, "max_y": max_y},
        "nan_fraction":  float(np.isnan(dem).sum() / dem.size),
        "elevation_min": float(np.nanmin(dem)),
        "elevation_max": float(np.nanmax(dem)),
    }
