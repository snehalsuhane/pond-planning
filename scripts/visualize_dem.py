"""
visualize_dem.py — Generate a terrain plot from a KML/KMZ file.

Usage:
    python visualize_dem.py <path_to_file.kml>

Outputs:
    dem_visualization.png  — side-by-side DEM image + contour map view
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib
matplotlib.use("Agg")   # headless — no display required
import matplotlib.pyplot as plt
import numpy as np

from utils.kml_parser import parse, KMLParseError
from analysis.terrain import analyze_contours, TerrainValidationError
from utils.projection import project_contours
from analysis.dem import generate_dem, DEMGenerationError

OUTPUT = "dem_visualization.png"
SEP = "=" * 55


def main():
    if len(sys.argv) < 2:
        print("Usage: python visualize_dem.py <file.kml or .kmz>")
        sys.exit(1)

    filepath = sys.argv[1]

    print(f"\n{SEP}")
    print("  DEM Visualisation")
    print(f"{SEP}")
    print(f"  File : {filepath}\n")

    # ── Parse ────────────────────────────────────────────────────────────────
    try:
        contours = parse(filepath)
    except KMLParseError as e:
        print(f"❌  Parse failed: {e}")
        sys.exit(1)
    print(f"✅  Parsed {len(contours)} contours")

    # ── Validate terrain ─────────────────────────────────────────────────────
    try:
        terrain = analyze_contours(contours)
    except TerrainValidationError as e:
        print(f"❌  Terrain validation failed: {e}")
        sys.exit(1)

    # ── Project ───────────────────────────────────────────────────────────────
    projected, crs_info = project_contours(contours)
    print(f"✅  Projected to {crs_info['name']} (EPSG:{crs_info['epsg']})")

    # ── Generate DEM ──────────────────────────────────────────────────────────
    try:
        dem_result = generate_dem(projected)
    except DEMGenerationError as e:
        print(f"❌  DEM generation failed: {e}")
        sys.exit(1)

    rows, cols = dem_result["shape"]
    res = dem_result["resolution_m"]
    print(f"✅  DEM generated: {rows}×{cols} grid at {res:.1f} m resolution")
    print(f"    Elevation: {dem_result['elevation_min']:.1f} m → {dem_result['elevation_max']:.1f} m")
    print(f"    NaN fraction: {dem_result['nan_fraction']:.4f}\n")

    # ── Plot ──────────────────────────────────────────────────────────────────
    dem  = dem_result["dem"]
    gx   = dem_result["x_coords"]
    gy   = dem_result["y_coords"]
    b    = dem_result["bounds"]
    extent = [b["min_x"], b["max_x"], b["min_y"], b["max_y"]]

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    fig.suptitle(
        f"Terrain DEM  |  {rows}×{cols} @ {res:.0f} m  |  "
        f"{dem_result['elevation_min']:.0f}–{dem_result['elevation_max']:.0f} m  |  "
        f"EPSG:{crs_info['epsg']}",
        fontsize=11,
    )

    # Left panel — DEM as shaded elevation image
    im = axes[0].imshow(
        dem, extent=extent, origin="lower", cmap="terrain", aspect="equal"
    )
    plt.colorbar(im, ax=axes[0], label="Elevation (m)", shrink=0.8)
    axes[0].set_title("Interpolated Elevation Surface")
    axes[0].set_xlabel("Easting (m)")
    axes[0].set_ylabel("Northing (m)")

    # Right panel — contour map reconstructed from the DEM
    xx, yy = np.meshgrid(gx, gy)
    n_levels = max(5, int(dem_result["elevation_max"] - dem_result["elevation_min"]) + 1)
    n_levels = min(n_levels, 30)   # cap for readability

    cf = axes[1].contourf(xx, yy, dem, levels=n_levels, cmap="terrain")
    cl = axes[1].contour(xx, yy, dem,  levels=n_levels,
                         colors="black", linewidths=0.4, alpha=0.5)
    plt.colorbar(cf, ax=axes[1], label="Elevation (m)", shrink=0.8)
    axes[1].set_title("Contour Map View (reconstructed from DEM)")
    axes[1].set_xlabel("Easting (m)")
    axes[1].set_ylabel("Northing (m)")
    try:
        axes[1].clabel(cl, inline=True, fontsize=6, fmt="%.0f m")
    except Exception:
        pass   # clabel can fail on very dense contours — non-fatal

    plt.tight_layout()
    plt.savefig(OUTPUT, dpi=150, bbox_inches="tight")
    print(f"✅  Saved → {OUTPUT}")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
