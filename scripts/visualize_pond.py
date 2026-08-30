"""
visualize_pond.py — Visualize terrain + slope + top N pond candidates.

Usage:
    python scripts/visualize_pond.py <path_to_file.kml>

Outputs:
    pond_candidates.png  — 3-panel figure:
        Left   : DEM elevation surface with top N candidates marked
        Centre : Slope map with candidates marked
        Right  : Zoomed view around the #1 candidate site
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
from analysis.hydrology import run_hydrology
from analysis.pond import rank_pond_candidates, PondCandidateError

OUTPUT = "pond_candidates.png"
SEP = "=" * 58


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/visualize_pond.py <file.kml or .kmz>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"\n{SEP}\n  Top N Pond Candidates Visualisation\n{SEP}")
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
    epsg = crs_info["epsg"]
    print(f"✅  Projected  →  EPSG:{epsg}")

    try:
        dem_result = generate_dem(projected)
    except DEMGenerationError as e:
        print(f"❌  DEM: {e}"); sys.exit(1)
    print(f"✅  DEM  {dem_result['shape']}  @  {dem_result['resolution_m']:.1f} m")

    slope_result = calculate_slope(dem_result)
    print(f"✅  Slope  {slope_result['slope_min']:.1f}°–{slope_result['slope_max']:.1f}°"
          f"  (mean {slope_result['slope_mean']:.1f}°)")

    try:
        result = rank_pond_candidates(dem_result, slope_result, epsg, num_candidates=10, min_distance_m=100.0)
    except PondCandidateError as e:
        print(f"❌  Candidates: {e}"); sys.exit(1)

    candidates = result["pond_candidates"]
    
    print(f"\n  ── Top {len(candidates)} Pond Candidates ──────────────────────────────────")
    for c in candidates:
        print(f"  #{c['rank']:<2} | Lat/Lon: {c['latitude']:.6f}, {c['longitude']:.6f} | "
              f"Elev: {c['elevation_m']:>5.1f}m | Slope: {c['slope_deg']:>4.1f}°")

    # ── Plot ─────────────────────────────────────────────────────────────────
    dem   = dem_result["dem"]
    slope = slope_result["slope"]
    gx    = dem_result["x_coords"]
    gy    = dem_result["y_coords"]
    b     = dem_result["bounds"]
    ext   = [b["min_x"], b["max_x"], b["min_y"], b["max_y"]]

    top_site = candidates[0]
    
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(
        f"Top {len(candidates)} Pond Candidates  |  #1 at lat={top_site['latitude']:.5f}, lon={top_site['longitude']:.5f}",
        fontsize=12,
    )

    # ── Panel 1: DEM with candidates ──────────────────────────────────────────
    im0 = axes[0].imshow(dem, extent=ext, origin="lower", cmap="terrain", aspect="equal")
    plt.colorbar(im0, ax=axes[0], label="Elevation (m)", shrink=0.8)
    axes[0].set_title("DEM + Candidates")
    axes[0].set_xlabel("Easting (m)")
    axes[0].set_ylabel("Northing (m)")
    
    for c in candidates[::-1]:  # reverse so #1 is plotted last (on top)
        cx = float(gx[c["grid_col"]])
        cy = float(gy[c["grid_row"]])
        color = "red" if c["rank"] == 1 else "black"
        size = 18 if c["rank"] == 1 else 12
        axes[0].plot(cx, cy, marker="*", color=color, markersize=size, zorder=5)
        axes[0].text(cx + 15, cy + 15, f"#{c['rank']}", color=color, fontweight="bold", fontsize=10, zorder=6)

    # ── Panel 2: Slope map with candidates ────────────────────────────────────
    im1 = axes[1].imshow(slope, extent=ext, origin="lower", cmap="YlOrRd", aspect="equal")
    plt.colorbar(im1, ax=axes[1], label="Slope (°)", shrink=0.8)
    axes[1].set_title("Slope Map + Candidates")
    axes[1].set_xlabel("Easting (m)")
    axes[1].set_ylabel("Northing (m)")
    
    for c in candidates[::-1]:
        cx = float(gx[c["grid_col"]])
        cy = float(gy[c["grid_row"]])
        color = "blue" if c["rank"] == 1 else "black"
        size = 18 if c["rank"] == 1 else 12
        axes[1].plot(cx, cy, marker="*", color=color, markersize=size, zorder=5)
        axes[1].text(cx + 15, cy + 15, f"#{c['rank']}", color=color, fontweight="bold", fontsize=10, zorder=6)

    # ── Panel 3: Zoomed DEM around #1 candidate ─────────────────────────────────
    cx_1 = float(gx[top_site["grid_col"]])
    cy_1 = float(gy[top_site["grid_row"]])
    zoom_m = max(300.0, dem_result["resolution_m"] * 30)
    x0, x1 = cx_1 - zoom_m, cx_1 + zoom_m
    y0, y1 = cy_1 - zoom_m, cy_1 + zoom_m
    xi0 = max(0, np.searchsorted(gx, x0) - 1)
    xi1 = min(len(gx), np.searchsorted(gx, x1) + 1)
    yi0 = max(0, np.searchsorted(gy, y0) - 1)
    yi1 = min(len(gy), np.searchsorted(gy, y1) + 1)
    
    dem_zoom = dem[yi0:yi1, xi0:xi1]
    ext_zoom = [gx[xi0], gx[xi1 - 1], gy[yi0], gy[yi1 - 1]]

    im2 = axes[2].imshow(dem_zoom, extent=ext_zoom, origin="lower",
                         cmap="terrain", aspect="equal")
    axes[2].plot(cx_1, cy_1, "r*", markersize=20, label="#1 Candidate", zorder=5)
    plt.colorbar(im2, ax=axes[2], label="Elevation (m)", shrink=0.8)
    axes[2].set_title(f"Zoomed View around #1 (±{zoom_m:.0f} m)")
    axes[2].set_xlabel("Easting (m)")
    axes[2].set_ylabel("Northing (m)")
    axes[2].legend(loc="upper right", fontsize=8)

    plt.tight_layout()
    plt.savefig(OUTPUT, dpi=150, bbox_inches="tight")
    print(f"\n✅  Saved → {OUTPUT}")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
