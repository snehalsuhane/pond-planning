"""
Utility: projection

Converts geographic coordinates (longitude, latitude in WGS 84) into a
projected coordinate system where units are metres.

Projection strategy
-------------------
UTM (Universal Transverse Mercator) is auto-selected from the centroid of
the input data.  The UTM zone is derived as:

    zone = floor((lon + 180) / 6) + 1
    hemisphere = 'north' if lat >= 0 else 'south'
    EPSG = 32600 + zone   (north)
          32700 + zone   (south)

This makes the module globally reusable — it works for any geographic
location without hardcoding a specific CRS.

Why UTM?
--------
- Industry standard for engineering / GIS work (QGIS, GDAL, SAGA all use it)
- < 0.04 % linear distortion within each 6° strip
- For a typical pond site (< 10 km across) the error is < 4 metres — well
  within the accuracy of field-surveyed contour data
- EPSG codes allow easy interoperability with downstream tools

Public API
----------
get_utm_epsg(lon, lat) -> int
    Return the EPSG code of the appropriate UTM zone for a given point.

project_coordinates(coords, transformer) -> list[list[float]]
    Transform a list of [lon, lat] pairs → [X_m, Y_m] pairs.

project_contours(contours) -> tuple[list[dict], dict]
    Add 'projected_coordinates' to every contour dict and return
    (projected_contours, crs_info).
"""

import math
from pyproj import Transformer, CRS


# ---------------------------------------------------------------------------
# UTM zone selection
# ---------------------------------------------------------------------------

def get_utm_epsg(lon: float, lat: float) -> int:
    """
    Return the EPSG code of the UTM zone that covers (lon, lat).

    Parameters
    ----------
    lon : float  Longitude in decimal degrees [-180, 180]
    lat : float  Latitude  in decimal degrees [ -90,  90]

    Returns
    -------
    int  EPSG code, e.g. 32644 for WGS 84 / UTM zone 44N
    """
    zone = math.floor((lon + 180.0) / 6.0) + 1
    # Special UTM rules for Norway/Svalbard (rarely needed, but correct)
    if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
        zone = 32
    if 72.0 <= lat <= 84.0:
        if   0.0 <= lon <  9.0: zone = 31
        elif 9.0 <= lon < 21.0: zone = 33
        elif 21.0 <= lon < 33.0: zone = 35
        elif 33.0 <= lon < 42.0: zone = 37

    base = 32600 if lat >= 0 else 32700
    return base + zone


def get_utm_crs_info(epsg: int) -> dict:
    """
    Return a human-readable CRS info dict for a given EPSG code.

    Returns
    -------
    dict with keys: epsg, name, unit
    """
    crs = CRS.from_epsg(epsg)
    return {
        "epsg": epsg,
        "name": crs.name,
        "unit": "metre",
    }


# ---------------------------------------------------------------------------
# Coordinate transformation
# ---------------------------------------------------------------------------

def _make_transformer(epsg: int) -> Transformer:
    """
    Build a pyproj Transformer from WGS 84 (EPSG:4326) to the target CRS.

    always_xy=True ensures input order is always (longitude, latitude),
    matching the [lon, lat] convention used throughout this project.
    """
    return Transformer.from_crs(
        "EPSG:4326",
        f"EPSG:{epsg}",
        always_xy=True,
    )


def project_coordinates(coords: list, transformer: Transformer) -> list:
    """
    Transform a list of [lon, lat] pairs into [X_metres, Y_metres] pairs.

    Parameters
    ----------
    coords      : list of [lon, lat]
    transformer : a pyproj Transformer (WGS 84 → target CRS)

    Returns
    -------
    list of [X, Y] pairs (floats, in metres)
    """
    projected = []
    for lon, lat in coords:
        x, y = transformer.transform(lon, lat)
        projected.append([x, y])
    return projected


# ---------------------------------------------------------------------------
# Public API — contour-level projection
# ---------------------------------------------------------------------------

def project_contours(contours: list) -> tuple:
    """
    Add projected metre coordinates to every contour dict.

    The UTM zone is determined once from the centroid of all coordinates
    in the dataset, then applied uniformly to every point.

    Original [lon, lat] coordinates are preserved unchanged under the
    'coordinates' key. Projected coordinates are added under the new
    'projected_coordinates' key.

    Parameters
    ----------
    contours : list[dict]
        Contour dicts as produced by kml_parser.parse() and validated by
        terrain.analyze_contours().  Each dict must have a 'coordinates'
        key containing a list of [lon, lat] pairs.

    Returns
    -------
    (projected_contours, crs_info)

        projected_contours : list[dict]
            Same structure as input but each dict also contains:
                "projected_coordinates": [[X1, Y1], [X2, Y2], ...]
            where X/Y are in metres in the auto-selected UTM CRS.

        crs_info : dict
            {"epsg": int, "name": str, "unit": "metre"}
            Describes the CRS that was applied.

    Raises
    ------
    ValueError
        If contours is empty or contains no usable coordinates.
    """
    if not contours:
        raise ValueError("Cannot project an empty contour list.")

    # ── 1. Compute dataset centroid for zone selection ────────────────────────
    all_lons = [pt[0] for c in contours for pt in c["coordinates"]]
    all_lats = [pt[1] for c in contours for pt in c["coordinates"]]

    if not all_lons:
        raise ValueError("No coordinate points found in contour list.")

    centroid_lon = sum(all_lons) / len(all_lons)
    centroid_lat = sum(all_lats) / len(all_lats)

    # ── 2. Select UTM zone & build transformer ───────────────────────────────
    epsg = get_utm_epsg(centroid_lon, centroid_lat)
    transformer = _make_transformer(epsg)
    crs_info = get_utm_crs_info(epsg)

    # ── 3. Project every contour ─────────────────────────────────────────────
    projected_contours = []
    for contour in contours:
        projected = dict(contour)                       # shallow copy — preserves all original keys
        projected["projected_coordinates"] = project_coordinates(
            contour["coordinates"], transformer
        )
        projected_contours.append(projected)

    return projected_contours, crs_info
