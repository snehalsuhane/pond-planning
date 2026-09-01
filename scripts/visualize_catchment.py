import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib
matplotlib.use("Agg")   # headless — no display required
import matplotlib.pyplot as plt
import numpy as np
import warnings

from utils.kml_parser import parse
from analysis.terrain import analyze_contours, calculate_slope
from utils.projection import project_contours
from analysis.dem import generate_dem
from analysis.hydrology import run_hydrology
from analysis.pond import rank_pond_candidates
from analysis.catchment import delineate_catchment, _REVERSE_D8
from collections import deque
from scipy.ndimage import binary_dilation

# Minimum shaded display area (m²). Catchments smaller than this are dilated
# for display only so every candidate has a clearly visible coloured zone.
_MIN_DISPLAY_M2 = 5000.0   # ~0.5 ha visible radius on a 6 m grid


def _hillshade(dem: np.ndarray, azimuth_deg: float = 315.0, altitude_deg: float = 45.0) -> np.ndarray:
    az  = np.radians(360.0 - azimuth_deg)
    alt = np.radians(altitude_deg)
    dy, dx = np.gradient(dem)
    slope  = np.arctan(np.sqrt(dx**2 + dy**2))
    aspect = np.arctan2(-dy, dx)
    hs = (np.sin(alt) * np.cos(slope)
          + np.cos(alt) * np.sin(slope) * np.cos(az - aspect))
    return np.clip(hs, 0, 1)


def _build_catchment_mask(fdir: np.ndarray, pour_point: tuple) -> np.ndarray:
    """BFS upstream trace — wraps the same logic used in delineate_catchment."""
    rows, cols = fdir.shape
    r_start, c_start = pour_point
    mask = np.zeros((rows, cols), dtype=bool)
    queue = deque([pour_point])
    mask[r_start, c_start] = True
    while queue:
        r, c = queue.popleft()
        for (dr, dc), rev_code in _REVERSE_D8.items():
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and not mask[nr, nc]:
                if fdir[nr, nc] == rev_code:
                    mask[nr, nc] = True
                    queue.append((nr, nc))
    return mask


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/visualize_catchment.py <path_to_kml>")
        sys.exit(1)

    kml_path = sys.argv[1]

    print("=" * 60)
    print("  Catchment Delineation Visualisation")
    print("=" * 60)
    print(f"  File : {kml_path}\n")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            contours = parse(kml_path)
            analyze_contours(contours)
            proj_contours, crs_info = project_contours(contours)
            dem_result = generate_dem(proj_contours)
            slope_result = calculate_slope(dem_result)
            hydro = run_hydrology(dem_result)
            epsg = crs_info["epsg"]
            candidates = rank_pond_candidates(dem_result, slope_result, epsg)

            fdir = hydro["flow_direction"]

            all_masks = []
            for candidate in candidates["pond_candidates"]:
                pour_point = (candidate["grid_row"], candidate["grid_col"])
                # Delineate catchment (polygon + area) via the shared module
                catchment = delineate_catchment(hydro, pour_point, dem_result, epsg)
                candidate["catchment"] = catchment
                # Build raster mask for visualization using the same BFS logic
                mask = _build_catchment_mask(fdir, pour_point)
                all_masks.append((candidate, mask))

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    print(f"✅  Parsed {len(contours)} contours")
    print(f"✅  Projected  →  EPSG:{epsg}")
    print(f"✅  Identified {len(all_masks)} Candidates and their Catchments\n")

    print(f"  {'Rank':<5} {'Lat':>10} {'Lon':>10} {'Elev':>8} {'Slope':>7} {'Area (m²)':>12}")
    print("  " + "-" * 60)
    for candidate, _ in all_masks:
        c = candidate
        area_m2 = c["catchment"]["area_m2"]
        print(f"  #{c['rank']:<4} {c['latitude']:>10.6f} {c['longitude']:>10.6f} "
              f"{c['elevation_m']:>7.1f}m {c['slope_deg']:>6.1f}°  {area_m2:>10.0f} m²")

    # ── Plotting ─────────────────────────────────────────────────────────────
    dem = dem_result["dem"]
    gx  = dem_result["x_coords"]
    gy  = dem_result["y_coords"]
    ext = [gx[0], gx[-1], gy[0], gy[-1]]
    hs  = _hillshade(dem)

    fig, ax = plt.subplots(figsize=(12, 9))

    # Hillshaded DEM base — reduced alpha so catchment colors show through clearly
    ax.imshow(hs, extent=ext, origin="lower", cmap="gray",
              vmin=0, vmax=1, aspect="equal")
    im = ax.imshow(dem, cmap="terrain", extent=ext, origin="lower", alpha=0.35)
    plt.colorbar(im, ax=ax, label="Elevation (m)", shrink=0.7, pad=0.01)

    # Catchment masks — use Set1 (highly saturated) for maximum contrast
    # Tiny masks (single / few cells) are dilated display-only so every
    # candidate has a visible shaded zone. True area is shown in the label.
    res = dem_result["resolution_m"]
    min_px = int(np.ceil(np.sqrt(_MIN_DISPLAY_M2 / (res ** 2))))
    struct = np.ones((min_px, min_px), dtype=bool)   # square dilation kernel

    colors = matplotlib.colormaps["Set1"].resampled(max(len(all_masks), 9))
    for i, (candidate, mask) in enumerate(all_masks):
        color = colors(i % 9)
        area_m2 = candidate["catchment"]["area_m2"]

        # Dilate if the true catchment is too small to see clearly
        display_mask = mask if area_m2 >= _MIN_DISPLAY_M2 else binary_dilation(mask, structure=struct)

        ws_rgba = np.zeros((*display_mask.shape, 4))
        ws_rgba[display_mask, 0] = color[0]
        ws_rgba[display_mask, 1] = color[1]
        ws_rgba[display_mask, 2] = color[2]
        ws_rgba[display_mask, 3] = 0.72
        ax.imshow(ws_rgba, extent=ext, origin="lower")

        px = float(gx[candidate["grid_col"]])
        py = float(gy[candidate["grid_row"]])
        label = f"#{candidate['rank']} — {area_m2:.0f} m²"

        if candidate["rank"] == 1:
            ax.scatter(px, py, color=color, marker="*", s=400,
                       edgecolor="white", linewidths=1.2, label=label, zorder=10)
        else:
            ax.scatter(px, py, color=color, marker="o", s=140,
                       edgecolor="white", linewidths=0.8, label=label, zorder=9)

    ax.set_title(
        f"Catchment Delineation — Top {len(all_masks)} Candidates"
        f"  |  EPSG:{epsg}",
        fontsize=11,
    )
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.axis("on")

    # Move legend outside the plot so it doesn't overlap the map
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.08, 1.0),
        borderaxespad=0.0,
        fontsize=9,
        title="Candidate (catchment area)",
        title_fontsize=9,
    )

    plt.tight_layout()
    out_file = "catchment_visualization.png"
    plt.savefig(out_file, dpi=150, bbox_inches="tight")
    print(f"\n✅  Saved → {out_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
