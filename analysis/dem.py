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

import heapq
import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter


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
        valid_mask    - bool grid inside the contour vertices' convex hull
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
        "valid_mask":    ~nan_mask,
        "extrapolated_fraction": float(nan_mask.mean()),
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


# ---------------------------------------------------------------------------
# DEM Conditioning for Hydrology
# ---------------------------------------------------------------------------

_NEIGHBOURS_8 = [(-1, -1), (-1, 0), (-1, 1),
                 ( 0, -1),          ( 0, 1),
                 ( 1, -1), ( 1, 0), ( 1, 1)]


def fill_depressions(dem: np.ndarray, epsilon: float = 0.0,
                     outlet_mask: np.ndarray | None = None) -> np.ndarray:
    """
    Fill depressions/pits in a 2D DEM using the Priority-Flood algorithm
    (Barnes, Lehman, Mulla 2014; Wang & Liu 2006).

    Floods the terrain inwards from the grid perimeter using a min-heap
    priority queue. Every interior cell in a depression is raised to its
    minimum spill elevation, guaranteeing a monotonic non-increasing path
    to the boundary.

    Parameters
    ----------
    dem : np.ndarray (rows, cols)
        Input elevation grid.
    outlet_mask : bool ndarray, optional
        Known real sinks to preserve as drainage terminals.
    epsilon : float, optional
        Optional elevation increment added to filled cells (default 0.0).

    Returns
    -------
    np.ndarray (rows, cols)
        Conditioned DEM with depressions filled.
    """
    if dem.ndim != 2:
        raise ValueError(f"Expected 2D DEM array, got shape {dem.shape}")

    rows, cols = dem.shape
    if rows <= 2 or cols <= 2:
        return np.copy(dem)

    filled = np.copy(dem).astype(float)
    visited = np.zeros((rows, cols), dtype=bool)
    heap = []

    # Initialize priority queue with all boundary cells
    for r in range(rows):
        for c in (0, cols - 1):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (float(filled[r, c]) if np.isfinite(filled[r, c]) else -np.inf, r, c))

    for c in range(1, cols - 1):
        for r in (0, rows - 1):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (float(filled[r, c]) if np.isfinite(filled[r, c]) else -np.inf, r, c))

    # Also push any NaN cells as boundary sinks if present
    nan_mask = np.isnan(filled)
    if nan_mask.any():
        nan_rows, nan_cols = np.where(nan_mask)
        for r, c in zip(nan_rows, nan_cols):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (-np.inf, r, c))

    # Known real sinks remain drainage terminals rather than being filled away.
    if outlet_mask is not None:
        for r, c in np.argwhere(outlet_mask & np.isfinite(filled)):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (float(filled[r, c]) if np.isfinite(filled[r, c]) else -np.inf, r, c))

    # Process cells from lowest spill elevation inward
    while heap:
        spill_elev, r, c = heapq.heappop(heap)

        for dr, dc in _NEIGHBOURS_8:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and not visited[nr, nc]:
                visited[nr, nc] = True
                n_elev = float(filled[nr, nc])
                if n_elev < spill_elev:
                    new_elev = spill_elev + epsilon
                    filled[nr, nc] = new_elev
                    heapq.heappush(heap, (new_elev, nr, nc))
                else:
                    heapq.heappush(heap, (n_elev, nr, nc))

    return filled


def smooth_dem(dem: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """
    Lightly smooth a DEM with a Gaussian filter to reduce stair-stepping
    and micro-pits from linear contour interpolation before hydrologic routing.

    Parameters
    ----------
    dem : np.ndarray (rows, cols)
        Input elevation grid.
    sigma : float, optional
        Gaussian kernel standard deviation (default 1.0).

    Returns
    -------
    np.ndarray (rows, cols)
        Smoothed elevation grid.
    """
    if dem.ndim != 2:
        raise ValueError(f"Expected 2D DEM array, got shape {dem.shape}")

    if sigma <= 0:
        return np.copy(dem)

    return gaussian_filter(dem.astype(float), sigma=sigma, mode="reflect")
