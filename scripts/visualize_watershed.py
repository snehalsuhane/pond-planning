import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib.pyplot as plt
import numpy as np
import warnings

from utils.kml_parser import parse
from analysis.terrain import analyze_contours, calculate_slope
from utils.projection import project_contours
from analysis.dem import generate_dem
from analysis.hydrology import run_hydrology
from analysis.pond import rank_pond_candidates
from analysis.watershed import delineate_watershed

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/visualize_watershed.py <path_to_kml>")
        sys.exit(1)

    kml_path = sys.argv[1]

    print("=" * 60)
    print("  Watershed Delineation Visualisation")
    print("=" * 60)
    print(f"  File : {kml_path}\n")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            contours = parse(kml_path)
            terrain = analyze_contours(contours)
            proj_contours, crs_info = project_contours(contours)
            dem_result = generate_dem(proj_contours)
            slope_result = calculate_slope(dem_result)
            hydro = run_hydrology(dem_result)
            epsg = crs_info["epsg"]
            candidates = rank_pond_candidates(dem_result, slope_result, epsg)
            
            # Use internal raster logic from delineate_watershed to get mask for visualisation
            from analysis.watershed import _REVERSE_D8
            from collections import deque
            
            fdir = hydro["flow_direction"]
            rows, cols = fdir.shape
            
            all_masks = []
            
            for candidate in candidates["pond_candidates"]:
                pour_point = (candidate["grid_row"], candidate["grid_col"])
                mask = np.zeros((rows, cols), dtype=bool)
                queue = deque([pour_point])
                mask[pour_point[0], pour_point[1]] = True
                
                while queue:
                    r, c = queue.popleft()
                    for (dr, dc), rev_code in _REVERSE_D8.items():
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < rows and 0 <= nc < cols:
                            if not mask[nr, nc]:
                                if fdir[nr, nc] == rev_code:
                                    mask[nr, nc] = True
                                    queue.append((nr, nc))
                all_masks.append((candidate, mask))
                # Also generate the polygon data to update candidate
                candidate["watershed"] = delineate_watershed(hydro, pour_point, dem_result, epsg)

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    print(f"✅  Parsed {len(contours)} contours")
    print(f"✅  Projected  →  EPSG:{epsg}")
    print(f"✅  Identified {len(all_masks)} Candidates and their Catchments")

    # Plotting
    dem = dem_result["dem"]
    gx = dem_result["x_coords"]
    gy = dem_result["y_coords"]
    ext = [gx[0], gx[-1], gy[0], gy[-1]]
    
    fig, ax = plt.subplots(figsize=(10, 8))
    
    # Base DEM
    im = ax.imshow(dem, cmap="terrain", extent=ext, origin="lower", alpha=0.8)
    
    # Overlay channel network
    chan_mask = hydro["channel_mask"]
    chan_rgba = np.zeros((*chan_mask.shape, 4))
    chan_rgba[chan_mask, 0] = 0.0  # R (blue)
    chan_rgba[chan_mask, 1] = 0.0  # G
    chan_rgba[chan_mask, 2] = 1.0  # B
    chan_rgba[chan_mask, 3] = 0.5  # Alpha
    ax.imshow(chan_rgba, extent=ext, origin="lower")
    
    # Overlay watershed masks
    colors = plt.cm.get_cmap("tab10", len(all_masks))
    for i, (candidate, mask) in enumerate(all_masks):
        ws_rgba = np.zeros((*mask.shape, 4))
        color = colors(i)
        ws_rgba[mask, 0] = color[0]  # R
        ws_rgba[mask, 1] = color[1]  # G
        ws_rgba[mask, 2] = color[2]  # B
        ws_rgba[mask, 3] = 0.4       # Alpha
        ax.imshow(ws_rgba, extent=ext, origin="lower")
        
        # Mark pour point
        pour_point = (candidate["grid_row"], candidate["grid_col"])
        px = float(gx[pour_point[1]])
        py = float(gy[pour_point[0]])
        
        # Highlight #1 candidate specifically
        if candidate["rank"] == 1:
            ax.scatter(px, py, color=color, marker='*', s=300, edgecolor='black', label=f"#{candidate['rank']} (Area: {candidate['watershed']['area_m2']:,.0f} m²)", zorder=10)
        else:
            ax.scatter(px, py, color=color, marker='o', s=100, edgecolor='black', label=f"#{candidate['rank']} (Area: {candidate['watershed']['area_m2']:,.0f} m²)", zorder=9)
    
    ax.set_title("Watershed Delineation for Top 10 Candidates")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.axis("off")
    ax.legend(loc="upper right")
    
    plt.tight_layout()
    out_file = "watershed_visualization.png"
    plt.savefig(out_file, dpi=150)
    print(f"\n✅  Saved → {out_file}")
    print("=" * 60)

if __name__ == "__main__":
    main()
