"""
visualize_pond.py — Visualize terrain, slope, TPI, and top N pond candidates.

Usage:
    python scripts/visualize_pond.py <path_to_file.kml>

Outputs:
    pond_candidates.png  — 3-panel figure:
        Left   : Hillshaded DEM with all candidates (rainbow-ranked, score annotated)
        Centre : Slope map with candidates marked
        Right  : Zoomed TPI (depression) map around the #1 candidate site
                 — blue = depression (ideal), red = ridge
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from utils.kml_parser import parse, KMLParseError
from analysis.terrain import analyze_contours, TerrainValidationError, calculate_slope
from utils.projection import project_contours
from analysis.dem import generate_dem, DEMGenerationError
from analysis.pond import rank_pond_candidates, PondCandidateError, _build_score

OUTPUT = "pond_candidates.png"
SEP = "=" * 58


def _hillshade(dem: np.ndarray, azimuth_deg: float = 315.0, altitude_deg: float = 45.0) -> np.ndarray:
    az  = np.radians(360.0 - azimuth_deg)
    alt = np.radians(altitude_deg)
    dy, dx = np.gradient(dem)
    slope  = np.arctan(np.sqrt(dx**2 + dy**2))
    aspect = np.arctan2(-dy, dx)
    hs = (np.sin(alt) * np.cos(slope)
          + np.cos(alt) * np.sin(slope) * np.cos(az - aspect))
    return np.clip(hs, 0, 1)


def _rank_color(rank: int, n: int) -> tuple:
    """Return a color from a rainbow palette; rank 1 = bright red."""
    cmap = matplotlib.colormaps["rainbow_r"].resampled(n)
    return cmap((rank - 1) / max(n - 1, 1))


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {os.path.basename(__file__)} <file.kml or .kmz>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"\n{SEP}\n  Top N Pond Candidates Visualisation\n{SEP}")
    print(f"  File : {filepath}\n")

    # ── Pipeline ─────────────────────────────────────────────────────────────
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
        result = rank_pond_candidates(dem_result, slope_result, epsg,
                                      num_candidates=10, min_distance_m=100.0)
    except PondCandidateError as e:
        print(f"❌  Candidates: {e}"); sys.exit(1)

    candidates = result["pond_candidates"]
    n = len(candidates)

    # Compute TPI grid for the zoom panel
    score_data = _build_score(
        dem_result["dem"], slope_result["slope"], dem_result["resolution_m"],
        elev_weight=0.3, slope_weight=0.4, depr_weight=0.3, depr_window_m=100.0
    )
    tpi_grid = score_data["tpi"]

    print(f"\n  ── Top {n} Pond Candidates {'─' * 40}")
    print(f"  {'#':<3} {'Lat':>10} {'Lon':>10} {'Elev':>7} {'Slope':>6} {'TPI':>7} {'Score':>6}")
    print("  " + "─" * 58)
    for c in candidates:
        print(f"  #{c['rank']:<2} {c['latitude']:>10.6f} {c['longitude']:>10.6f} "
              f"{c['elevation_m']:>6.1f}m {c['slope_deg']:>5.1f}° "
              f"{c['tpi']:>7.2f} {c['score']:>6.3f}")

    # ── Arrays ───────────────────────────────────────────────────────────────
    dem   = dem_result["dem"]
    slope = slope_result["slope"]
    gx    = dem_result["x_coords"]
    gy    = dem_result["y_coords"]
    b     = dem_result["bounds"]
    ext   = [b["min_x"], b["max_x"], b["min_y"], b["max_y"]]
    hs    = _hillshade(dem)
    top   = candidates[0]

    # ── Figure: 1×3 ──────────────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(20, 7))
    fig.suptitle(
        f"Top {n} Pond Candidates  |  #1 at lat={top['latitude']:.5f}, lon={top['longitude']:.5f}"
        f"  |  score={top['score']:.3f}  tpi={top['tpi']:.1f}",
        fontsize=12, fontweight="bold",
    )

    # ── Panel 1: Hillshaded DEM + ranked candidates ──────────────────────────
    ax = axes[0]
    ax.imshow(hs, extent=ext, origin="lower", cmap="gray", vmin=0, vmax=1, aspect="equal")
    im0 = ax.imshow(dem, extent=ext, origin="lower", cmap="terrain", aspect="equal", alpha=0.55)
    plt.colorbar(im0, ax=ax, label="Elevation (m)", shrink=0.8)
    ax.set_title("Hillshaded DEM — Pond Candidates (ranked)")
    ax.set_xlabel("Easting (m)"); ax.set_ylabel("Northing (m)")

    for c in candidates[::-1]:   # reverse so #1 renders on top
        color = _rank_color(c["rank"], n)
        cx = float(gx[c["grid_col"]])
        cy = float(gy[c["grid_row"]])
        mk = "*" if c["rank"] == 1 else "o"
        sz = 280 if c["rank"] == 1 else 100
        ax.scatter(cx, cy, color=color, marker=mk, s=sz,
                   edgecolors="white", linewidths=0.8, zorder=6)
        ax.text(cx + 20, cy + 20,
                f"#{c['rank']}\n{c['score']:.3f}",
                color="white", fontsize=7, fontweight="bold",
                bbox=dict(facecolor="black", alpha=0.45, pad=1.5, edgecolor="none"),
                zorder=7)

    # ── Panel 2: Slope map + candidates ──────────────────────────────────────
    ax = axes[1]
    im1 = ax.imshow(slope, extent=ext, origin="lower",
                    cmap="YlOrRd", aspect="equal",
                    vmin=0, vmax=min(30, slope_result["slope_max"]))
    plt.colorbar(im1, ax=ax, label="Slope (°)", shrink=0.8)
    ax.set_title("Slope Map — Candidates Marked")
    ax.set_xlabel("Easting (m)"); ax.set_ylabel("Northing (m)")

    for c in candidates[::-1]:
        color = _rank_color(c["rank"], n)
        cx = float(gx[c["grid_col"]])
        cy = float(gy[c["grid_row"]])
        mk = "*" if c["rank"] == 1 else "o"
        sz = 280 if c["rank"] == 1 else 100
        ax.scatter(cx, cy, color=color, marker=mk, s=sz,
                   edgecolors="black", linewidths=0.6, zorder=6)
        ax.text(cx + 20, cy + 20, f"#{c['rank']}",
                color="black", fontsize=7, fontweight="bold", zorder=7)

    # ── Panel 3: Zoomed TPI (depression map) around #1 ───────────────────────
    ax = axes[2]
    cx_1 = float(gx[top["grid_col"]])
    cy_1 = float(gy[top["grid_row"]])
    zoom_m = max(400.0, dem_result["resolution_m"] * 50)
    xi0 = max(0, int(np.searchsorted(gx, cx_1 - zoom_m)) - 1)
    xi1 = min(len(gx) - 1, int(np.searchsorted(gx, cx_1 + zoom_m)) + 1)
    yi0 = max(0, int(np.searchsorted(gy, cy_1 - zoom_m)) - 1)
    yi1 = min(len(gy) - 1, int(np.searchsorted(gy, cy_1 + zoom_m)) + 1)

    tpi_zoom = tpi_grid[yi0:yi1, xi0:xi1]
    hs_zoom  = hs[yi0:yi1, xi0:xi1]
    ext_zoom = [gx[xi0], gx[xi1], gy[yi0], gy[yi1]]

    ax.imshow(hs_zoom, extent=ext_zoom, origin="lower",
              cmap="gray", vmin=0, vmax=1, aspect="equal")
    vlim = max(abs(tpi_zoom.min()), abs(tpi_zoom.max()), 1.0)
    im2 = ax.imshow(tpi_zoom, extent=ext_zoom, origin="lower",
                    cmap="RdBu", vmin=-vlim, vmax=vlim,
                    aspect="equal", alpha=0.7)
    plt.colorbar(im2, ax=ax, label="TPI — Depression (blue) / Ridge (red)", shrink=0.8)

    for c in candidates[::-1]:
        ccx = float(gx[c["grid_col"]])
        ccy = float(gy[c["grid_row"]])
        if ext_zoom[0] <= ccx <= ext_zoom[1] and ext_zoom[2] <= ccy <= ext_zoom[3]:
            color = _rank_color(c["rank"], n)
            mk = "*" if c["rank"] == 1 else "o"
            sz = 400 if c["rank"] == 1 else 120
            ax.scatter(ccx, ccy, color=color, marker=mk, s=sz,
                       edgecolors="black", linewidths=0.8, zorder=8)
            ax.text(ccx + 15, ccy + 15, f"#{c['rank']}",
                    color="white", fontsize=8, fontweight="bold",
                    bbox=dict(facecolor="black", alpha=0.5, pad=1.5, edgecolor="none"),
                    zorder=9)

    ax.set_title(f"TPI Zoom ±{zoom_m:.0f} m around #1 Candidate\n"
                 f"Blue = depression (ideal pond site)  |  Red = ridge")
    ax.set_xlabel("Easting (m)"); ax.set_ylabel("Northing (m)")

    plt.tight_layout()
    plt.savefig(OUTPUT, dpi=150, bbox_inches="tight")
    print(f"\n✅  Saved → {OUTPUT}\n{SEP}\n")


if __name__ == "__main__":
    main()
