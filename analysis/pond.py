"""
Analysis: pond

Identifies suitable pond candidate locations from a DEM and slope grid.

Selection algorithm
-------------------
Each grid cell is scored by combining two normalised criteria:

    score = w_elev * elev_norm + w_slope * slope_norm

where:
    elev_norm  = (elevation - min_elevation) / elevation_range   [0=low, 1=high]
    slope_norm = slope_deg / max_slope_deg_in_grid               [0=flat, 1=steep]

Lower score → better pond site (low-lying and gentle terrain).

Cells that exceed max_slope_deg or lie within the edge border are excluded.
The cell with the minimum score is selected and its projected (X, Y) centre
is converted back to geographic (lon, lat) using pyproj.

Public API
----------
find_pond_candidate(dem_result, slope_result, epsg,
                    max_slope_deg, elev_weight, slope_weight, border_cells)
    → dict with pond_site information
"""

import numpy as np
from pyproj import Transformer


class PondCandidateError(ValueError):
    """Raised when no suitable pond candidate can be identified."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_score(dem: np.ndarray, slope: np.ndarray,
                 elev_weight: float, slope_weight: float) -> np.ndarray:
    """Return a per-cell suitability score (lower = better)."""
    elev_range = dem.max() - dem.min()
    elev_norm  = (dem - dem.min()) / elev_range if elev_range > 0 else np.zeros_like(dem)

    slope_max  = slope.max()
    slope_norm = slope / slope_max if slope_max > 0 else np.zeros_like(slope)

    return elev_weight * elev_norm + slope_weight * slope_norm


def _border_mask(shape: tuple, border: int) -> np.ndarray:
    """Return a boolean mask that is True for the outer `border` cells."""
    mask = np.zeros(shape, dtype=bool)
    if border > 0:
        mask[:border, :]  = True
        mask[-border:, :] = True
        mask[:, :border]  = True
        mask[:, -border:] = True
    return mask


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_pond_candidate(
    dem_result:   dict,
    slope_result: dict,
    epsg:         int,
    max_slope_deg: float = 8.0,
    elev_weight:   float = 0.6,
    slope_weight:  float = 0.4,
    border_cells:  int   = 3,
) -> dict:
    """
    Identify the most suitable pond candidate cell.

    Parameters
    ----------
    dem_result   : dict from analysis.dem.generate_dem()
    slope_result : dict from analysis.terrain.calculate_slope()
    epsg         : EPSG code of the projected CRS (used for back-projection)
    max_slope_deg: cells steeper than this are excluded (default 8°)
    elev_weight  : weight for elevation criterion (default 0.6)
    slope_weight : weight for slope criterion    (default 0.4)
    border_cells : cell margin excluded from selection (default 3)

    Returns
    -------
    dict:
        pond_site → {latitude, longitude, elevation_m, slope_deg,
                     grid_row, grid_col}

    Raises
    ------
    PondCandidateError  if no valid candidate exists after masking.
    """
    dem      = dem_result["dem"]
    x_coords = dem_result["x_coords"]
    y_coords = dem_result["y_coords"]
    slope    = slope_result["slope"]

    # ── Score grid ───────────────────────────────────────────────────────────
    score = _build_score(dem, slope, elev_weight, slope_weight)

    # ── Exclusion masks ──────────────────────────────────────────────────────
    steep_mask  = slope > max_slope_deg
    border_mask = _border_mask(dem.shape, border_cells)
    excluded    = steep_mask | border_mask

    if excluded.all():
        raise PondCandidateError(
            f"No valid cells remain after applying slope threshold "
            f"({max_slope_deg}°) and border exclusion ({border_cells} cells). "
            "Try increasing max_slope_deg or reducing border_cells."
        )

    # Apply exclusion by setting excluded cells to infinity
    score[excluded] = np.inf

    # ── Select best cell ─────────────────────────────────────────────────────
    best_flat = int(np.argmin(score))
    row, col  = np.unravel_index(best_flat, score.shape)

    proj_x = float(x_coords[col])
    proj_y = float(y_coords[row])

    # ── Back-project to lon/lat ──────────────────────────────────────────────
    transformer = Transformer.from_crs(
        f"EPSG:{epsg}", "EPSG:4326", always_xy=True
    )
    lon, lat = transformer.transform(proj_x, proj_y)

    return {
        "pond_site": {
            "latitude":    round(float(lat), 7),
            "longitude":   round(float(lon), 7),
            "elevation_m": round(float(dem[row, col]),  2),
            "slope_deg":   round(float(slope[row, col]), 2),
            "grid_row":    int(row),
            "grid_col":    int(col),
        }
    }
