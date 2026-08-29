"""
visualize_hydrology.py — DEM + flow accumulation + channel network.

Usage:
    python scripts/visualize_hydrology.py <file.kml>

Outputs:
    hydrology.png  — 3-panel figure:
        Left   : DEM terrain surface
        Centre : Flow accumulation (log scale)
        Right  : Drainage channel network overlaid on DEM
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from utils.kml_parser import parse, KMLParseError
from analysis.terrain import analyze_contours, TerrainValidationError
from utils.projection import project_contours
from analysis.dem import generate_dem, DEMGenerationError
from analysis.hydrology import run_hydrology

OUTPUT = "hydrology.png"
SEP = "=" * 58


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/visualize_hydrology.py <file.kml or .kmz>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"\n{SEP}\n  Hydrology Visualisation\n{SEP}")
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
    print(f"✅  Projected  →  EPSG:{crs_info['epsg']}")

    try:
        dem_result = generate_dem(projected)
    except DEMGenerationError as e:
        print(f"❌  DEM: {e}"); sys.exit(1)
    print(f"✅  DEM {dem_result['shape']} @ {dem_result['resolution_m']:.1f} m")

    hydro = run_hydrology(dem_result)
    acc   = hydro["flow_accumulation"]
    fdir  = hydro["flow_direction"]
    chan  = hydro["channel_mask"]

    print(f"✅  Flow direction  —  {hydro['noflow_count']} pit/flat cells")
    print(f"✅  Flow accumulation  —  max={hydro['acc_max']:.0f}, mean={hydro['acc_mean']:.1f}")
    print(f"✅  Channels  —  {hydro['channel_cell_count']} cells "
          f"({hydro['channel_fraction']*100:.2f}%)  "
          f"threshold={hydro['channel_threshold']:.1f}\n")

    # ── Plot ─────────────────────────────────────────────────────────────────
    dem = dem_result["dem"]
    b   = dem_result["bounds"]
    ext = [b["min_x"], b["max_x"], b["min_y"], b["max_y"]]

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(
        f"Hydrology  |  {dem_result['shape']}  @  {dem_result['resolution_m']:.0f} m  "
        f"|  EPSG:{crs_info['epsg']}",
        fontsize=11,
    )

    # Panel 1 — DEM
    im0 = axes[0].imshow(dem, extent=ext, origin="lower", cmap="terrain", aspect="equal")
    plt.colorbar(im0, ax=axes[0], label="Elevation (m)", shrink=0.8)
    axes[0].set_title("Terrain DEM")
    axes[0].set_xlabel("Easting (m)"); axes[0].set_ylabel("Northing (m)")

    # Panel 2 — Flow accumulation (log scale)
    log_acc = np.log1p(acc)
    im1 = axes[1].imshow(log_acc, extent=ext, origin="lower", cmap="Blues", aspect="equal")
    cb1 = plt.colorbar(im1, ax=axes[1], label="log(1 + accumulation)", shrink=0.8)
    axes[1].set_title(f"Flow Accumulation (log)  |  max={hydro['acc_max']:.0f}")
    axes[1].set_xlabel("Easting (m)"); axes[1].set_ylabel("Northing (m)")

    # Panel 3 — Channel network on DEM
    axes[2].imshow(dem, extent=ext, origin="lower", cmap="terrain",
                   aspect="equal", alpha=0.7)
    # Overlay channel mask as red pixels
    chan_rgba = np.zeros((*dem.shape, 4), dtype=np.float32)
    chan_rgba[chan, 0] = 0.9   # R
    chan_rgba[chan, 2] = 0.2   # B
    chan_rgba[chan, 3] = 0.9   # A (opaque where channel)
    axes[2].imshow(chan_rgba, extent=ext, origin="lower", aspect="equal")
    axes[2].set_title(
        f"Drainage Channels  |  {hydro['channel_cell_count']} cells  "
        f"(threshold ≥ {hydro['channel_threshold']:.0f})"
    )
    axes[2].set_xlabel("Easting (m)"); axes[2].set_ylabel("Northing (m)")

    plt.tight_layout()
    plt.savefig(OUTPUT, dpi=150, bbox_inches="tight")
    print(f"✅  Saved → {OUTPUT}")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
