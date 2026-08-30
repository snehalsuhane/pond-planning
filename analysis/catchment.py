"""
Analysis: catchment

Delineates the upstream catchment area for a specific pour point 
using the D8 flow direction grid, and vectorizes it into a geographic polygon.
"""

import numpy as np
from collections import deque
import contourpy
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
    epsg: int
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
                if not mask[nr, nc]:
                    # If this neighbour's flow direction points towards (r, c)
                    if fdir[nr, nc] == rev_code:
                        mask[nr, nc] = True
                        count += 1
                        queue.append((nr, nc))

    # 2. Area Calculation
    res = dem_result["resolution_m"]
    area_m2 = float(count * (res ** 2))

    # 3. Vectorize the mask using contourpy
    mask_float = mask.astype(float)
    c_gen = contourpy.contour_generator(z=mask_float)
    lines = c_gen.lines(0.5)

    if not lines:
        return {
            "area_m2": area_m2,
            "area_ha": round(area_m2 / 10000.0, 4),
            "area_km2": round(area_m2 / 1000000.0, 6),
            "polygon": []
        }

    # Extract the longest closed contour line (to ignore internal holes/artifacts)
    longest_line = max(lines, key=len)

    # 4. Project (col, row) pixel indices to geographic [lon, lat]
    gx = dem_result["x_coords"]
    gy = dem_result["y_coords"]
    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    
    polygon = []
    for (col_idx, row_idx) in longest_line:
        # contourpy returns floats for exact interpolated boundaries.
        # We linearly interpolate the projected X, Y coordinates:
        x_proj = gx[0] + col_idx * res
        y_proj = gy[0] + row_idx * res
        
        lon, lat = transformer.transform(x_proj, y_proj)
        polygon.append([round(float(lon), 6), round(float(lat), 6)])

    return {
        "area_m2": area_m2,
        "area_ha": round(area_m2 / 10000.0, 4),
        "area_km2": round(area_m2 / 1000000.0, 6),
        "polygon": polygon
    }
