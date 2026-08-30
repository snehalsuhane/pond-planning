"""
Analysis: hydrology

D8 flow direction, flow accumulation, and drainage channel detection.

Note: This module currently operates as an independent component. It provides 
valuable terrain metadata and lays the groundwork for future catchment delineation, 
but is intentionally decoupled from the core pond candidate scoring algorithm.

Public API
----------
calculate_flow_direction(dem_result)    -> dict
calculate_flow_accumulation(fdir_result) -> dict
detect_channels(facc_result, threshold) -> dict
run_hydrology(dem_result, channel_threshold) -> dict   # convenience wrapper
"""

from collections import deque
import numpy as np

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


# ---------------------------------------------------------------------------
# Flow direction  (D8)
# ---------------------------------------------------------------------------

def calculate_flow_direction(dem_result: dict) -> dict:
    """
    Assign a D8 flow direction to every DEM cell.

    Each cell is directed toward the steepest downslope neighbour.
    Cells with no downslope neighbour (pits / local minima) receive
    direction code 0.

    Parameters
    ----------
    dem_result : dict from analysis.dem.generate_dem()

    Returns
    -------
    dict:
        flow_direction  – np.ndarray int16  (rows, cols), ArcGIS D8 codes
        noflow_count    – int, number of pit/flat cells (code 0)
        shape           – tuple
    """
    dem = dem_result["dem"]
    res = dem_result["resolution_m"]
    rows, cols = dem.shape

    padded = np.pad(dem, 1, mode="edge")   # (rows+2, cols+2)

    # Compute slope toward each of 8 neighbours; shape (8, rows, cols)
    slopes = np.full((8, rows, cols), -np.inf)
    codes  = []

    for i, (code, (dr, dc, is_diag)) in enumerate(_D8.items()):
        dist = res * (np.sqrt(2) if is_diag else 1.0)
        r0, c0 = 1 + dr, 1 + dc
        neighbour = padded[r0:r0 + rows, c0:c0 + cols]
        slopes[i] = (dem - neighbour) / dist
        codes.append(code)

    codes_arr = np.array(codes, dtype=np.int16)
    best_idx  = np.argmax(slopes, axis=0)          # (rows, cols)
    fdir      = codes_arr[best_idx]

    # Cells where the maximum slope is ≤ 0 have no downslope neighbour
    fdir[slopes.max(axis=0) <= 0] = 0

    return {
        "flow_direction": fdir,
        "noflow_count":   int((fdir == 0).sum()),
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
    acc   = np.ones(n, dtype=np.float64)
    queue = deque(np.where(in_deg == 0)[0].tolist())

    while queue:
        i = queue.popleft()
        if not is_sink[i]:
            j = int(recv_flat[i])
            acc[j] += acc[i]
            in_deg[j] -= 1
            if in_deg[j] == 0:
                queue.append(j)

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
        threshold = float(np.percentile(acc, 99))

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

def run_hydrology(dem_result: dict, channel_threshold: float | None = None) -> dict:
    """
    Run the full hydrology pipeline: flow direction → accumulation → channels.

    Parameters
    ----------
    dem_result        : dict from analysis.dem.generate_dem()
    channel_threshold : passed directly to detect_channels()

    Returns
    -------
    dict with keys:
        flow_direction, flow_accumulation, channel_mask,
        channel_threshold, channel_cell_count, channel_fraction,
        noflow_count, acc_max, acc_mean
    """
    fdir_result = calculate_flow_direction(dem_result)
    facc_result = calculate_flow_accumulation(fdir_result)
    chan_result  = detect_channels(facc_result, threshold=channel_threshold)

    return {
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
