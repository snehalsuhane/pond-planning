"""
Analysis: hydrology

D8 flow direction, flow accumulation, and drainage channel detection.

This module is an integrated step in the full analysis pipeline:
  DEM → slope → hydrology (this module) → pond candidates → catchment

The flow direction grid produced here is consumed by analysis.catchment
for upstream BFS tracing, and the channel mask and accumulation stats
are returned in the API response under the ``hydrology`` key.

Public API
----------
calculate_flow_direction(dem_result, fill_pits=True, smooth=False, sigma=1.0) -> dict
resolve_flats(dem, slopes, res, preserved) -> np.ndarray
calculate_flow_accumulation(fdir_result) -> dict
detect_channels(facc_result, threshold) -> dict
run_hydrology(dem_result, channel_threshold=None, fill_pits=True, ...) -> dict
"""

from collections import deque
import numpy as np
from analysis.dem import fill_depressions, smooth_dem

# ---------------------------------------------------------------------------
# D8 encoding: ArcGIS/TauDEM convention
#   code -> (row_offset, col_offset, is_diagonal)
# ---------------------------------------------------------------------------
_D8 = {
      1: ( 0,  1, False),   # E
      2: ( 1,  1, True),    # SE
      4: ( 1,  0, False),   # S
      8: ( 1, -1, True),    # SW
     16: ( 0, -1, False),   # W
     32: (-1, -1, True),    # NW
     64: (-1,  0, False),   # N
    128: (-1,  1, True),    # NE
}

_DRDC = [(-1, -1), (-1, 0), (-1, 1),
         ( 0, -1),          ( 0, 1),
         ( 1, -1), ( 1, 0), ( 1, 1)]


# ---------------------------------------------------------------------------
# Flat resolution using multi-source distance to outlets
# ---------------------------------------------------------------------------

def resolve_flats(dem: np.ndarray, slopes: np.ndarray, res: float,
                  outlet_mask: np.ndarray | None = None) -> np.ndarray:
    """Route equal-elevation cells by decreasing integer distance to an outlet.

    All downhill edges and open domain edges seed a multi-source BFS. Original
    downhill directions are retained; only exactly equal elevations can receive
    a synthetic slope. Thus every edge decreases either elevation or flat
    distance, including when elevations differ by floating-point roundoff.
    No elevation epsilon is added to the terrain.
    """
    rows, cols = dem.shape
    valid = np.isfinite(dem)
    downhill = slopes.max(axis=0) > 0
    boundary = np.zeros_like(valid)
    boundary[[0, -1], :] = True
    boundary[:, [0, -1]] = True
    padded_valid = np.pad(valid, 1, constant_values=False)
    for dr, dc in _DRDC:
        boundary |= ~padded_valid[1+dr:1+dr+rows, 1+dc:1+dc+cols]
    preserved = np.zeros_like(valid) if outlet_mask is None else outlet_mask & valid
    distance = np.full(dem.shape, -1, dtype=np.int32)

    def propagate(seeds):
        distance[seeds] = 0
        queue = deque(map(tuple, np.argwhere(seeds)))
        while queue:
            r, c = queue.popleft()
            for dr, dc in _DRDC:
                nr, nc = r + dr, c + dc
                if (0 <= nr < rows and 0 <= nc < cols and valid[nr, nc]
                        and distance[nr, nc] < 0 and dem[nr, nc] == dem[r, c]):
                    distance[nr, nc] = distance[r, c] + 1
                    queue.append((nr, nc))

    # Prefer known downhill exits. Only otherwise-undrained flats use the
    # domain edge as an open outlet; this avoids stealing flow near a border.
    propagate(valid & (downhill | preserved))
    outlets = (boundary & valid & (distance < 0)) | preserved
    propagate(outlets & (distance < 0))

    result = slopes.copy()
    unresolved = valid & ~downhill & ~outlets & (distance > 0)
    padded_dem = np.pad(dem, 1, constant_values=np.nan)
    padded_distance = np.pad(distance, 1, constant_values=-1)
    for i, (_, (dr, dc, diagonal)) in enumerate(_D8.items()):
        neighbour = padded_dem[1+dr:1+dr+rows, 1+dc:1+dc+cols]
        neighbour_distance = padded_distance[1+dr:1+dr+rows, 1+dc:1+dc+cols]
        eligible = (unresolved & (neighbour == dem) & (neighbour_distance >= 0)
                    & (neighbour_distance < distance))
        result[i, eligible] = ((distance[eligible] - neighbour_distance[eligible])
                               / (res * (np.sqrt(2) if diagonal else 1.0)))
    result[:, outlets] = 0
    return result


# ---------------------------------------------------------------------------
# Flow direction  (D8)
# ---------------------------------------------------------------------------

def calculate_flow_direction(
    dem_result: dict,
    fill_pits: bool = True,
    smooth: bool = False,
    sigma: float = 1.0,
) -> dict:
    """
    Assign a D8 flow direction to every DEM cell.

    When ``fill_pits=True``, fills depressions (Priority-Flood), then
    resolves flat surfaces to guarantee continuous drainage.

    Each cell is directed toward the steepest downslope neighbour.
    Cells with no downslope neighbour (unresolved pits / boundary sinks)
    receive direction code 0.

    Parameters
    ----------
    dem_result : dict from analysis.dem.generate_dem()
    fill_pits : bool, optional
        Whether to fill depressions and resolve flats (default True).
    smooth : bool, optional
        Whether to apply light Gaussian smoothing before filling (default False).
    sigma : float, optional
        Gaussian kernel std-dev for smoothing (default 1.0).

    Returns
    -------
    dict:
        flow_direction  – np.ndarray int16  (rows, cols), ArcGIS D8 codes
        noflow_count    – int, number of pit/flat cells (code 0)
        shape           – tuple
    """
    raw_dem = dem_result["dem"]
    res = dem_result["resolution_m"]
    rows, cols = raw_dem.shape
    if not np.isfinite(res) or res <= 0:
        raise ValueError("resolution_m must be positive and finite")
    valid = np.asarray(dem_result.get("valid_mask", np.isfinite(raw_dem)), dtype=bool)
    if valid.shape != raw_dem.shape or not valid.any():
        raise ValueError("valid_mask must match the DEM and contain valid cells")
    valid = valid & np.isfinite(raw_dem)
    if not valid.any():
        raise ValueError("DEM has no finite valid cells")
    preserved = np.asarray(dem_result.get("preserved_sink_mask", np.zeros_like(valid)), dtype=bool)
    if preserved.shape != raw_dem.shape:
        raise ValueError("preserved_sink_mask must match the DEM")

    if fill_pits:
        work_dem = smooth_dem(raw_dem, sigma=sigma) if smooth else raw_dem
        dem = fill_depressions(np.where(valid, work_dem, np.nan), outlet_mask=preserved)
    else:
        dem = raw_dem

    dem = np.where(valid, dem, np.nan)
    padded = np.pad(dem, 1, constant_values=np.nan)   # (rows+2, cols+2)

    # Compute slope toward each of 8 neighbours; shape (8, rows, cols)
    slopes = np.full((8, rows, cols), -np.inf)
    codes  = []

    for i, (code, (dr, dc, is_diag)) in enumerate(_D8.items()):
        dist = res * (np.sqrt(2) if is_diag else 1.0)
        r0, c0 = 1 + dr, 1 + dc
        neighbour = padded[r0:r0 + rows, c0:c0 + cols]
        slopes[i] = np.where(np.isfinite(neighbour) & valid, (dem - neighbour) / dist, -np.inf)
        codes.append(code)

    if fill_pits:
        slopes = resolve_flats(dem, slopes, res, preserved)

    codes_arr = np.array(codes, dtype=np.int16)
    best_idx  = np.argmax(slopes, axis=0)          # (rows, cols)
    fdir      = codes_arr[best_idx]

    # Cells where the maximum slope is ≤ 0 have no downslope neighbour
    fdir[slopes.max(axis=0) <= 0] = 0

    fdir[~valid | preserved] = 0

    return {
        "valid_mask": valid,
        "conditioned_dem": dem,
        "flow_direction": fdir,
        "noflow_count":   int(((fdir == 0) & valid).sum()),
        "shape":          fdir.shape,
    }


# ---------------------------------------------------------------------------
# Flow accumulation
# ---------------------------------------------------------------------------

def calculate_flow_accumulation(fdir_result: dict) -> dict:
    """
    Compute D8 flow accumulation via topological sort.

    Every cell starts with a value of 1 (itself).  Water is routed
    downstream according to the flow-direction grid; each cell's
    total accumulation is the count of all upstream cells that drain
    through it (including itself).

    Parameters
    ----------
    fdir_result : dict from calculate_flow_direction()

    Returns
    -------
    dict:
        flow_accumulation – np.ndarray float64  (rows, cols)
        acc_max           – float
        acc_mean          – float
        shape             – tuple
    """
    fdir = fdir_result["flow_direction"]
    rows, cols = fdir.shape
    n = rows * cols
    valid = np.asarray(fdir_result.get("valid_mask", np.ones_like(fdir, dtype=bool)))
    if not np.isin(fdir, [0, *_D8]).all():
        raise ValueError("Unknown D8 direction code")

    # ── Build receiver index (vectorised) ────────────────────────────────────
    R = np.repeat(np.arange(rows, dtype=np.int32), cols)
    C = np.tile(np.arange(cols, dtype=np.int32), rows)

    recv_r = R.copy()
    recv_c = C.copy()

    fdir_flat = fdir.ravel()
    for code, (dr, dc, _) in _D8.items():
        m = fdir_flat == code
        recv_r[m] = np.clip(R[m] + dr, 0, rows - 1)
        recv_c[m] = np.clip(C[m] + dc, 0, cols - 1)

    recv_flat = (recv_r * cols + recv_c).astype(np.int64)
    src_flat  = np.arange(n, dtype=np.int64)
    is_sink   = (recv_flat == src_flat)     # no-flow or pit cells

    # ── In-degree ─────────────────────────────────────────────────────────────
    in_deg = np.zeros(n, dtype=np.int32)
    np.add.at(in_deg, recv_flat[~is_sink], 1)

    # ── Topological-sort propagation ─────────────────────────────────────────
    acc   = valid.ravel().astype(np.float64)
    queue = deque(np.where(in_deg == 0)[0].tolist())

    while queue:
        i = queue.popleft()
        if not is_sink[i]:
            j = int(recv_flat[i])
            acc[j] += acc[i]
            in_deg[j] -= 1
            if in_deg[j] == 0:
                queue.append(j)

    if np.any(in_deg > 0):
        raise ValueError(f"Flow direction contains cycles ({np.count_nonzero(in_deg)} cells)")
    acc = acc.reshape(rows, cols)

    return {
        "flow_accumulation": acc,
        "acc_max":           float(acc.max()),
        "acc_mean":          float(acc.mean()),
        "shape":             acc.shape,
    }


# ---------------------------------------------------------------------------
# Channel detection
# ---------------------------------------------------------------------------

def detect_channels(facc_result: dict, threshold: float | None = None) -> dict:
    """
    Derive a drainage-channel mask from the flow-accumulation grid.

    Cells whose accumulation value meets or exceeds `threshold` are
    classified as channel cells.

    Parameters
    ----------
    facc_result : dict from calculate_flow_accumulation()
    threshold   : float or None
        Minimum accumulation value to be classified as a channel cell.
        If None, defaults to the 99th percentile of the accumulation grid
        (top 1 % of cells by upstream drainage area).

    Returns
    -------
    dict:
        channel_mask        – np.ndarray bool  (rows, cols)
        threshold           – float, value used
        channel_cell_count  – int
        channel_fraction    – float  (0–1)
    """
    acc = facc_result["flow_accumulation"]

    if threshold is None:
        positive = acc[acc > 0]
        threshold = float(np.percentile(positive, 99)) if positive.size else 1.0

    mask = acc >= threshold

    return {
        "channel_mask":       mask,
        "threshold":          float(threshold),
        "channel_cell_count": int(mask.sum()),
        "channel_fraction":   float(mask.mean()),
    }


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

def run_hydrology(
    dem_result: dict,
    channel_threshold: float | None = None,
    fill_pits: bool = True,
    smooth: bool = False,
    sigma: float = 1.0,
) -> dict:
    """
    Run the full hydrology pipeline: flow direction → accumulation → channels.

    Parameters
    ----------
    dem_result        : dict from analysis.dem.generate_dem()
    channel_threshold : passed directly to detect_channels()
    fill_pits         : bool, optional (default True)
        Whether to fill depressions and resolve flats before routing.
    smooth            : bool, optional (default False)
        Whether to apply light Gaussian smoothing before filling.
    sigma             : float, optional (default 1.0)
        Gaussian std-dev for smoothing.

    Returns
    -------
    dict with keys:
        flow_direction, flow_accumulation, channel_mask,
        channel_threshold, channel_cell_count, channel_fraction,
        noflow_count, acc_max, acc_mean
    """
    fdir_result = calculate_flow_direction(
        dem_result, fill_pits=fill_pits, smooth=smooth, sigma=sigma
    )
    facc_result = calculate_flow_accumulation(fdir_result)
    chan_result  = detect_channels(facc_result, threshold=channel_threshold)

    return {
        "valid_mask":          fdir_result["valid_mask"],
        "conditioned_dem":     fdir_result["conditioned_dem"],
        "flow_direction":      fdir_result["flow_direction"],
        "flow_accumulation":   facc_result["flow_accumulation"],
        "channel_mask":        chan_result["channel_mask"],
        "channel_threshold":   chan_result["threshold"],
        "channel_cell_count":  chan_result["channel_cell_count"],
        "channel_fraction":    chan_result["channel_fraction"],
        "noflow_count":        fdir_result["noflow_count"],
        "acc_max":             facc_result["acc_max"],
        "acc_mean":            facc_result["acc_mean"],
    }
