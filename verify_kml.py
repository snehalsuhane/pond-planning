"""
verify_kml.py — Manual verification tool for the KML/KMZ parser + terrain analyser.

Usage:
    python verify_kml.py <path_to_your_file.kml>
    python verify_kml.py <path_to_your_file.kmz>

What it does:
    1. Parses the file using utils/kml_parser.parse()
    2. Runs terrain validation + analysis using analysis/terrain.analyze_contours()
    3. Prints a rich summary to the terminal
    4. Saves the FULL raw contour list to parsed_output.json
       (open it to compare individual placemarks against the raw KML)
"""

import sys
import json
from utils.kml_parser import parse, KMLParseError
from analysis.terrain import analyze_contours, TerrainValidationError

SEP = "=" * 58
DIV = "-" * 58


def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_kml.py <path_to_file.kml or .kmz>")
        sys.exit(1)

    filepath = sys.argv[1]
    output_file = "parsed_output.json"

    print(f"\n{SEP}")
    print("  KML Parser + Terrain Analyser — Manual Verification")
    print(SEP)
    print(f"  File : {filepath}")
    print(f"{SEP}\n")

    # ── Step 1: Parse ─────────────────────────────────────────────────────────
    try:
        contours = parse(filepath)
    except KMLParseError as e:
        print(f"❌  KML Parse failed:\n    {e}\n")
        sys.exit(1)

    print(f"✅  Parsed {len(contours)} contour(s) from file.\n")

    # ── Step 2: Terrain analysis ──────────────────────────────────────────────
    try:
        terrain = analyze_contours(contours)
    except TerrainValidationError as e:
        print(f"❌  Terrain validation failed:\n    {e}\n")
        sys.exit(1)

    # ── Step 3: Print terrain summary ────────────────────────────────────────
    b = terrain["bounds"]
    interval_str = (
        f"{terrain['contour_interval_m']} m"
        + (" (uniform ✅)" if terrain["contour_interval_uniform"] else " (non-uniform ⚠️)")
    )

    print(f"{DIV}")
    print("  Terrain Metadata")
    print(f"{DIV}")
    print(f"  Contour count          : {terrain['contour_count']}")
    print(f"  Elevation range        : {terrain['min_elevation_m']} m  →  {terrain['max_elevation_m']} m")
    print(f"  Contour interval       : {interval_str}")
    print(f"  Total coordinate points: {terrain['total_points']}")
    print(f"  Bounds:")
    print(f"    Longitude  : {b['min_lon']}  →  {b['max_lon']}")
    print(f"    Latitude   : {b['min_lat']}  →  {b['max_lat']}")
    print(f"{DIV}\n")

    # ── Step 4: Preview first 5 contours ─────────────────────────────────────
    print("  First 5 contours (preview):")
    print(DIV)
    for c in contours[:5]:
        print(f"\n  ID        : {c['id']}")
        print(f"  Elevation : {c['elevation']} m")
        print(f"  Points    : {len(c['coordinates'])}")
        print(f"  First pt  : lon={c['coordinates'][0][0]}, lat={c['coordinates'][0][1]}")
        if len(c["coordinates"]) > 1:
            print(f"  Last pt   : lon={c['coordinates'][-1][0]}, lat={c['coordinates'][-1][1]}")

    # ── Step 5: Save full contour list to JSON ────────────────────────────────
    with open(output_file, "w") as f:
        json.dump(contours, f, indent=2)

    print(f"\n{SEP}")
    print(f"  Full contour data saved to '{output_file}'")
    print(f"  Open it and cross-check elevations/coords against the raw KML.")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
