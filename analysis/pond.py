"""
Analysis: pond

Identifies suitable pond candidate locations from a DEM and slope grid.

Selection algorithm
-------------------
Each grid cell is scored by combining three normalised criteria:

    score = w_elev * elev_norm + w_slope * slope_norm + w_depr * depr_norm

where:
    elev_norm  = (elevation - min) / range                 [0=low, 1=high]
    slope_norm = slope_deg / max_slope_deg                 [0=flat, 1=steep]
    depr_norm  = normalised TPI (Topographic Position Idx) [0=deep depression, 1=ridge/flat]

Lower score → better pond site.

Cells that exceed max_slope_deg, lie within the edge border, or contain NaNs are excluded.
Spatially distinct candidates are greedily selected based on minimum score and minimum distance.

Public API
----------
rank_pond_candidates(dem_result, slope_result, epsg, ...)
    → dict with a list of ranked pond_candidates

find_pond_candidate(...)
    → optional compatibility wrapper returning the single top candidate
"""

import numpy as np
from pyproj import Transformer
from scipy.ndimage import uniform_filter


class PondCandidateError(ValueError):
    """Raised when no suitable pond candidate can be identified."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_score(dem: np.ndarray, slope: np.ndarray, res: float,
                 elev_weight: float, slope_weight: float,
                 depr_weight: float, depr_window_m: float) -> np.ndarray:
    """Return a per-cell suitability score (lower = better)."""
    # 1. Elevation
    elev_range = dem.max() - dem.min()
    elev_norm  = (dem - dem.min()) / elev_range if elev_range > 0 else np.zeros_like(dem)

    # 2. Slope
    slope_max  = slope.max()
    slope_norm = slope / slope_max if slope_max > 0 else np.zeros_like(slope)

    # 3. Depression (TPI)
    window_px = max(3, int(depr_window_m / res))
    if window_px % 2 == 0:
        window_px += 1
        
    mean_dem = uniform_filter(dem, size=window_px, mode="reflect")
    tpi = dem - mean_dem
    
    # Cap TPI at 0 (so ridges and flats score the maximum penalty of 1.0)
    tpi_capped = np.minimum(tpi, 0.0)
    tpi_min = tpi_capped.min()
    
    if tpi_min < 0:
        # Deepest depression maps to 0.0, flat/ridge maps to 1.0
        depr_norm = 1.0 - (tpi_capped / tpi_min)
    else:
        depr_norm = np.ones_like(dem)

    return elev_weight * elev_norm + slope_weight * slope_norm + depr_weight * depr_norm


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

def rank_pond_candidates(
    dem_result:    dict,
    slope_result:  dict,
    epsg:          int,
    max_slope_deg:  float = 8.0,
    elev_weight:    float = 0.3,
    slope_weight:   float = 0.4,
    depr_weight:    float = 0.3,
    depr_window_m:  float = 100.0,
    border_cells:   int   = 3,
    num_candidates: int   = 10,
    min_distance_m: float = 100.0,
) -> dict:
    """
    Identify and rank top spatially distinct pond candidates.

    Parameters
    ----------
    dem_result     : dict from analysis.dem.generate_dem()
    slope_result   : dict from analysis.terrain.calculate_slope()
    epsg           : EPSG code of the projected CRS
    max_slope_deg  : cells steeper than this are excluded (default 8°)
    elev_weight    : weight for elevation criterion (default 0.3)
    slope_weight   : weight for slope criterion (default 0.4)
    depr_weight    : weight for local depression criterion (default 0.3)
    depr_window_m  : evaluation window for depressions in metres (default 100.0)
    border_cells   : cell margin excluded from selection (default 3)
    num_candidates : maximum number of candidates to return (default 10)
    min_distance_m : minimum distance between candidates in metres (default 100.0)

    Returns
    -------
    dict:
        candidates → list of candidate dicts

    Raises
    ------
    PondCandidateError  if no valid candidate exists after masking.
    """
    dem      = dem_result["dem"]
    x_coords = dem_result["x_coords"]
    y_coords = dem_result["y_coords"]
    res      = dem_result["resolution_m"]
    slope    = slope_result["slope"]

    # ── Score grid ───────────────────────────────────────────────────────────
    score = _build_score(dem, slope, res, elev_weight, slope_weight, depr_weight, depr_window_m)

    # ── Exclusion masks ──────────────────────────────────────────────────────
    excluded  = slope > max_slope_deg
    excluded |= _border_mask(dem.shape, border_cells)
    excluded |= ~np.isfinite(dem)

    if excluded.all():
        raise PondCandidateError(
            f"No valid cells remain after applying slope threshold "
            f"({max_slope_deg}°), border exclusion ({border_cells} cells), "
            "and NaN checks. Try increasing max_slope_deg or reducing border_cells."
        )

    # Apply exclusion by setting excluded cells to infinity
    score[excluded] = np.inf

    # ── Select Top N Spatially Distinct ──────────────────────────────────────
    valid_mask = np.isfinite(score)
    flat_indices = np.where(valid_mask.ravel())[0]
    valid_scores = score.ravel()[flat_indices]

    # Sort indices by score (lowest first)
    sort_idx = np.argsort(valid_scores)
    sorted_flat_indices = flat_indices[sort_idx]

    min_dist_px = min_distance_m / res
    selected_cells = []
    
    for idx in sorted_flat_indices:
        r, c = np.unravel_index(idx, score.shape)
        
        # Check distance against already selected candidates
        too_close = False
        for (cr, cc, _) in selected_cells:
            dist_px = np.sqrt((r - cr)**2 + (c - cc)**2)
            if dist_px < min_dist_px:
                too_close = True
                break
                
        if not too_close:
            cell_score = score[r, c]
            selected_cells.append((int(r), int(c), float(cell_score)))
            if len(selected_cells) >= num_candidates:
                break

    if not selected_cells:
        raise PondCandidateError("No valid candidates could be selected.")

    # ── Back-project and format results ──────────────────────────────────────
    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    
    candidates_list = []
    for rank, (row, col, cell_score) in enumerate(selected_cells, start=1):
        proj_x = float(x_coords[col])
        proj_y = float(y_coords[row])
        lon, lat = transformer.transform(proj_x, proj_y)
        
        # We can also compute TPI for output metadata if we want, but let's just stick to score
        candidates_list.append({
            "rank":              rank,
            "latitude":          round(float(lat), 7),
            "longitude":         round(float(lon), 7),
            "elevation_m":       round(float(dem[row, col]),  2),
            "slope_deg":         round(float(slope[row, col]), 2),
            "score":             round(cell_score, 3),
            "grid_row":          row,
            "grid_col":          col,
        })

    return {
        "pond_candidates": candidates_list
    }


def find_pond_candidate(
    dem_result:   dict,
    slope_result: dict,
    epsg:         int,
    max_slope_deg: float = 8.0,
    elev_weight:   float = 0.3,
    slope_weight:  float = 0.4,
    depr_weight:   float = 0.3,
    depr_window_m: float = 100.0,
    border_cells:  int   = 3,
) -> dict:
    """
    Compatibility wrapper returning a single pond_site.
    """
    result = rank_pond_candidates(
        dem_result=dem_result,
        slope_result=slope_result,
        epsg=epsg,
        max_slope_deg=max_slope_deg,
        elev_weight=elev_weight,
        slope_weight=slope_weight,
        depr_weight=depr_weight,
        depr_window_m=depr_window_m,
        border_cells=border_cells,
        num_candidates=1,
    )
    top_candidate = result["pond_candidates"][0]
    return {
        "pond_site": top_candidate
    }
