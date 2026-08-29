"""
visualize_pond.py — Visualize terrain + slope + pond candidate.

Usage:
    python scripts/visualize_pond.py <path_to_file.kml>

Outputs:
    pond_candidate.png  — 3-panel figure:
        Left   : DEM elevation surface with pond candidate marked
        Centre : Slope map with candidate marked
        Right  : Zoomed view around the candidate site
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from utils.kml_parser import parse, KMLParseError
from analysis.terrain import analyze_contours, TerrainValidationError, calculate_slope
from utils.projection import project_contours
from analysis.dem import generate_dem, DEMGenerationError
from analysis.pond import find_pond_candidate, PondCandidateError

OUTPUT = "pond_candidate.png"
SEP = "=" * 58


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/visualize_pond.py <file.kml or .kmz>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"\n{SEP}\n  Pond Candidate Visualisation\n{SEP}")
    print(f"  File : {filepath}\n")

    # ── Full pipeline ────────────────────────────────────────────────────────
    try:
        contours = parse(filepath)
    except KMLParseError as e:
        print(f"❌  Parse: {e}"); sys.exit(1)
    print(f"✅  Parsed {len(contours)} contours")

    try:
        analyze_contours(contours)
    except TerrainValidationError as e:
        print(f"❌  Terrain: {e}"); sys.exit(1)

    projected, crs_info = project_contours(contours)
    print(f"✅  Projected  →  EPSG:{crs_info['epsg']}")

    try:
        dem_result = generate_dem(projected)
    except DEMGenerationError as e:
        print(f"❌  DEM: {e}"); sys.exit(1)
    print(f"✅  DEM  {dem_result['shape']}  @  {dem_result['resolution_m']:.1f} m")

    slope_result = calculate_slope(dem_result)
    print(f"✅  Slope  {slope_result['slope_min']:.1f}°–{slope_result['slope_max']:.1f}°"
          f"  (mean {slope_result['slope_mean']:.1f}°)")

    try:
        candidate = find_pond_candidate(dem_result, slope_result, crs_info["epsg"])
    except PondCandidateError as e:
        print(f"❌  Candidate: {e}"); sys.exit(1)

    site = candidate["pond_site"]
    row, col = site["grid_row"], site["grid_col"]
    cx = float(dem_result["x_coords"][col])
    cy = float(dem_result["y_coords"][row])
    print(f"\n  ── Pond Candidate ──────────────────────────────────")
    print(f"  Lat / Lon   : {site['latitude']:.6f}, {site['longitude']:.6f}")
    print(f"  Elevation   : {site['elevation_m']:.1f} m")
    print(f"  Slope       : {site['slope_deg']:.1f}°")
    print(f"  Grid cell   : row={row}, col={col}\n")

    # ── Plot ─────────────────────────────────────────────────────────────────
    dem   = dem_result["dem"]
    slope = slope_result["slope"]
    gx    = dem_result["x_coords"]
    gy    = dem_result["y_coords"]
    b     = dem_result["bounds"]
    ext   = [b["min_x"], b["max_x"], b["min_y"], b["max_y"]]

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(
        f"Pond Candidate  |  lat={site['latitude']:.5f}, lon={site['longitude']:.5f}"
        f"  |  elev={site['elevation_m']:.1f} m  |  slope={site['slope_deg']:.1f}°",
        fontsize=11,
    )

    # ── Panel 1: DEM with candidate ──────────────────────────────────────────
    im0 = axes[0].imshow(dem, extent=ext, origin="lower", cmap="terrain", aspect="equal")
    axes[0].plot(cx, cy, "r*", markersize=16, label="Pond candidate", zorder=5)
    plt.colorbar(im0, ax=axes[0], label="Elevation (m)", shrink=0.8)
    axes[0].set_title("DEM + Candidate")
    axes[0].set_xlabel("Easting (m)")
    axes[0].set_ylabel("Northing (m)")
    axes[0].legend(loc="upper right", fontsize=8)

    # ── Panel 2: Slope map with candidate ────────────────────────────────────
    im1 = axes[1].imshow(slope, extent=ext, origin="lower", cmap="YlOrRd", aspect="equal")
    axes[1].plot(cx, cy, "b*", markersize=16, label="Pond candidate", zorder=5)
    plt.colorbar(im1, ax=axes[1], label="Slope (°)", shrink=0.8)
    axes[1].set_title("Slope Map + Candidate")
    axes[1].set_xlabel("Easting (m)")
    axes[1].set_ylabel("Northing (m)")
    axes[1].legend(loc="upper right", fontsize=8)

    # ── Panel 3: Zoomed DEM around candidate ─────────────────────────────────
    zoom_m = max(300.0, dem_result["resolution_m"] * 30)
    x0, x1 = cx - zoom_m, cx + zoom_m
    y0, y1 = cy - zoom_m, cy + zoom_m
    xi0 = max(0, np.searchsorted(gx, x0) - 1)
    xi1 = min(len(gx), np.searchsorted(gx, x1) + 1)
    yi0 = max(0, np.searchsorted(gy, y0) - 1)
    yi1 = min(len(gy), np.searchsorted(gy, y1) + 1)
    dem_zoom = dem[yi0:yi1, xi0:xi1]
    ext_zoom = [gx[xi0], gx[xi1 - 1], gy[yi0], gy[yi1 - 1]]

    im2 = axes[2].imshow(dem_zoom, extent=ext_zoom, origin="lower",
                         cmap="terrain", aspect="equal")
    axes[2].plot(cx, cy, "r*", markersize=20, label="Pond candidate", zorder=5)
    plt.colorbar(im2, ax=axes[2], label="Elevation (m)", shrink=0.8)
    axes[2].set_title(f"Zoomed View (±{zoom_m:.0f} m)")
    axes[2].set_xlabel("Easting (m)")
    axes[2].set_ylabel("Northing (m)")
    axes[2].legend(loc="upper right", fontsize=8)

    plt.tight_layout()
    plt.savefig(OUTPUT, dpi=150, bbox_inches="tight")
    print(f"✅  Saved → {OUTPUT}")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
