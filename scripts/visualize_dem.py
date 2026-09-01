"""
visualize_dem.py — Generate a terrain plot from a KML/KMZ file.

Usage:
    python scripts/visualize_dem.py <path_to_file.kml>

Outputs:
    dem_visualization.png  — 3-panel figure:
        Left   : Hillshaded DEM elevation surface
        Centre : Slope map (degrees)
        Right  : Contour map reconstructed from the DEM
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

from utils.kml_parser import parse, KMLParseError
from analysis.terrain import analyze_contours, TerrainValidationError, calculate_slope
from utils.projection import project_contours
from analysis.dem import generate_dem, DEMGenerationError

OUTPUT = "dem_visualization.png"
SEP = "=" * 55


def _hillshade(dem: np.ndarray, azimuth_deg: float = 315.0, altitude_deg: float = 45.0) -> np.ndarray:
    """Compute a hillshade array from a DEM (values 0–1)."""
    az  = np.radians(360.0 - azimuth_deg)
    alt = np.radians(altitude_deg)
    dy, dx = np.gradient(dem)
    slope  = np.arctan(np.sqrt(dx**2 + dy**2))
    aspect = np.arctan2(-dy, dx)
    hs = (np.sin(alt) * np.cos(slope)
          + np.cos(alt) * np.sin(slope) * np.cos(az - aspect))
    hs = np.clip(hs, 0, 1)
    return hs


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {os.path.basename(__file__)} <file.kml or .kmz>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"\n{SEP}\n  DEM Visualisation\n{SEP}")
    print(f"  File : {filepath}\n")

    try:
        contours = parse(filepath)
    except KMLParseError as e:
        print(f"❌  Parse failed: {e}"); sys.exit(1)
    print(f"✅  Parsed {len(contours)} contours")

    try:
        analyze_contours(contours)
    except TerrainValidationError as e:
        print(f"❌  Terrain validation failed: {e}"); sys.exit(1)

    projected, crs_info = project_contours(contours)
    print(f"✅  Projected to {crs_info['name']} (EPSG:{crs_info['epsg']})")

    try:
        dem_result = generate_dem(projected)
    except DEMGenerationError as e:
        print(f"❌  DEM generation failed: {e}"); sys.exit(1)

    slope_result = calculate_slope(dem_result)

    rows, cols = dem_result["shape"]
    res  = dem_result["resolution_m"]
    print(f"✅  DEM generated: {rows}×{cols} grid at {res:.1f} m/cell")
    print(f"    Elevation  : {dem_result['elevation_min']:.1f} m → {dem_result['elevation_max']:.1f} m")
    print(f"    Slope      : {slope_result['slope_min']:.1f}° → {slope_result['slope_max']:.1f}° (mean {slope_result['slope_mean']:.1f}°)\n")

    dem    = dem_result["dem"]
    slope  = slope_result["slope"]
    gx     = dem_result["x_coords"]
    gy     = dem_result["y_coords"]
    b      = dem_result["bounds"]
    extent = [b["min_x"], b["max_x"], b["min_y"], b["max_y"]]

    hs = _hillshade(dem)

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(
        f"Terrain DEM  |  {rows}×{cols} @ {res:.0f} m  |  "
        f"{dem_result['elevation_min']:.0f}–{dem_result['elevation_max']:.0f} m  |  EPSG:{crs_info['epsg']}",
        fontsize=11,
    )

    # ── Panel 1: Hillshaded DEM ───────────────────────────────────────────────
    axes[0].imshow(hs, extent=extent, origin="lower", cmap="gray",
                   vmin=0, vmax=1, aspect="equal")
    im0 = axes[0].imshow(dem, extent=extent, origin="lower",
                         cmap="terrain", aspect="equal", alpha=0.6)
    plt.colorbar(im0, ax=axes[0], label="Elevation (m)", shrink=0.8)
    axes[0].set_title("Hillshaded Elevation Surface")
    axes[0].set_xlabel("Easting (m)"); axes[0].set_ylabel("Northing (m)")

    # ── Panel 2: Slope map ────────────────────────────────────────────────────
    im1 = axes[1].imshow(slope, extent=extent, origin="lower",
                         cmap="YlOrRd", aspect="equal", vmin=0, vmax=min(45, slope_result["slope_max"]))
    plt.colorbar(im1, ax=axes[1], label="Slope (°)", shrink=0.8)
    axes[1].set_title(f"Slope Map  |  mean {slope_result['slope_mean']:.1f}°")
    axes[1].set_xlabel("Easting (m)"); axes[1].set_ylabel("Northing (m)")

    # ── Panel 3: Contour map ──────────────────────────────────────────────────
    # Use imshow as base (preserves aspect="equal" correctly) and
    # overlay contour lines on top.
    xx, yy = np.meshgrid(gx, gy)
    n_levels = min(30, max(5, int(dem_result["elevation_max"] - dem_result["elevation_min"]) + 1))
    im2 = axes[2].imshow(dem, extent=extent, origin="lower",
                         cmap="terrain", aspect="equal")
    plt.colorbar(im2, ax=axes[2], label="Elevation (m)", shrink=0.8)
    cl = axes[2].contour(xx, yy, dem, levels=n_levels,
                         colors="black", linewidths=0.5, alpha=0.6)
    axes[2].set_title("Contour Map (reconstructed from DEM)")
    axes[2].set_xlabel("Easting (m)"); axes[2].set_ylabel("Northing (m)")
    try:
        axes[2].clabel(cl, inline=True, fontsize=6, fmt="%.0f m")
    except Exception:
        pass

    plt.tight_layout()
    plt.savefig(OUTPUT, dpi=150, bbox_inches="tight")
    print(f"✅  Saved → {OUTPUT}\n{SEP}\n")


if __name__ == "__main__":
    main()
