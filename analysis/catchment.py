"""
Analysis: catchment

Delineates the upstream catchment area for a specific pour point 
using the D8 flow direction grid, and vectorizes it into a geographic polygon.
"""

import numpy as np
from collections import deque
from pyproj import Transformer
from analysis.hydrology import _D8

# ---------------------------------------------------------------------------
# Reverse D8 Mapping
# We need to map an (offset_row, offset_col) to the D8 code that points 
# *towards* the origin. If a neighbour is at (dr, dc) relative to the pour point,
# the vector from the neighbour to the pour point is (-dr, -dc). We want the 
# D8 code that corresponds to (-dr, -dc).
# ---------------------------------------------------------------------------
_REVERSE_D8 = {}
for code, (dr, dc, _) in _D8.items():
    for r_code, (r_dr, r_dc, _) in _D8.items():
        if r_dr == -dr and r_dc == -dc:
            _REVERSE_D8[(dr, dc)] = r_code
            break


def delineate_catchment(
    fdir_result: dict,
    pour_point: tuple[int, int],
    dem_result: dict,
    epsg: int,
) -> dict:
    """
    Delineate the catchment for a given pour point.

    Uses Breadth-First Search to trace all upstream cells based on the D8 
    flow direction. Converts the resulting mask into a geographic polygon.

    Parameters
    ----------
    fdir_result : dict from analysis.hydrology.calculate_flow_direction()
    pour_point  : tuple (grid_row, grid_col) representing the candidate site
    dem_result  : dict from analysis.dem.generate_dem()
    epsg        : int, UTM zone EPSG code for coordinate back-projection

    Returns
    -------
    dict:
        polygon        : list of [lon, lat] coordinates representing the boundary
        area_m2        : float, total area in square metres
        area_ha        : float, total area in hectares
        area_km2       : float, total area in square kilometres
    """
    fdir = fdir_result["flow_direction"]
    rows, cols = fdir.shape
    r_start, c_start = pour_point

    if not (0 <= r_start < rows and 0 <= c_start < cols):
        raise ValueError(f"Pour point {pour_point} is out of bounds for grid size {rows}x{cols}")

    valid = fdir_result.get("valid_mask", np.ones_like(fdir, dtype=bool))
    if not valid[r_start, c_start]:
        raise ValueError("Pour point is outside the surveyed domain")

    # 1. Tracing algorithm (BFS)
    mask = np.zeros((rows, cols), dtype=bool)
    queue = deque([pour_point])
    mask[r_start, c_start] = True
    
    count = 1

    while queue:
        r, c = queue.popleft()
        
        # Check all 8 neighbours
        for (dr, dc), rev_code in _REVERSE_D8.items():
            nr, nc = r + dr, c + dc
            
            # Check bounds and if already visited
            if 0 <= nr < rows and 0 <= nc < cols:
                if valid[nr, nc] and not mask[nr, nc]:
                    # If this neighbour's flow direction points towards (r, c)
                    if fdir[nr, nc] == rev_code:
                        mask[nr, nc] = True
                        count += 1
                        queue.append((nr, nc))

    # 2. Area Calculation
    res = dem_result["resolution_m"]
    area_m2 = float(count * (res ** 2))

    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(dem_result["x_coords"][c_start], dem_result["y_coords"][r_start])
    boundary = np.zeros_like(valid)
    boundary[[0, -1], :] = True
    boundary[:, [0, -1]] = True
    padded_valid = np.pad(valid, 1, constant_values=False)
    for dr, dc, _ in _D8.values():
        boundary |= ~padded_valid[1+dr:1+dr+rows, 1+dc:1+dc+cols]
    metadata = {
        "pour_point": {"latitude": float(lat), "longitude": float(lon)},
        "outlet_shift_m": 0.0,
        "boundary_truncated": bool((mask & boundary).any()),
    }

    from analysis.raster_geometry import mask_geometry
    return {
        **metadata,
        'area_m2': area_m2,
        'area_ha': round(area_m2 / 10000, 4),
        'area_km2': round(area_m2 / 1e6, 6),
        **mask_geometry(mask, dem_result, epsg),
    }


def trace_upstream_catchment(hydro: dict, target_mask: np.ndarray) -> np.ndarray:
    """Trace all cells draining to any collection-target cell, counting each once.

    The target may be a single outlet or a natural depression. Flow is absorbed
    on first entry; internal flat-routing branches do not split the catchment.
    """
    fdir = hydro['flow_direction']
    valid = hydro.get('valid_mask', np.ones_like(fdir, dtype=bool))
    if target_mask.shape != fdir.shape or not target_mask.any() or (target_mask & ~valid).any():
        raise ValueError('Collection target must contain valid terrain cells')
    mask = target_mask.copy()
    rows, cols = mask.shape
    queue = deque(map(tuple, np.argwhere(mask)))
    while queue:
        r, c = queue.popleft()
        for (dr, dc), code in _REVERSE_D8.items():
            nr, nc = r+dr, c+dc
            if (0 <= nr < rows and 0 <= nc < cols and valid[nr, nc]
                    and not mask[nr, nc] and fdir[nr, nc] == code):
                mask[nr, nc] = True
                queue.append((nr, nc))
    return mask
