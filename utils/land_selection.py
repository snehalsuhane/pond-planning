"""Validate selected land and mask candidate cells without clipping terrain."""
import json

import numpy as np
import shapely
from shapely.geometry import shape, mapping
from shapely.ops import transform
from pyproj import Transformer


class LandSelectionError(ValueError):
    """The selected boundary cannot be used with the supplied terrain."""


def parse_land_area(value):
    """Accept a WGS84 GeoJSON Polygon geometry or Feature, including holes."""
    try:
        data = json.loads(value) if isinstance(value, str) else value
        if not isinstance(data, dict):
            raise ValueError()
        if data.get('type') == 'Feature':
            data = data.get('geometry')
        if not isinstance(data, dict) or data.get('type') != 'Polygon':
            raise ValueError()
        rings = data['coordinates']
        if not rings or sum(len(ring) for ring in rings) > 10000:
            raise ValueError()
        for ring in rings:
            points = np.asarray(ring, dtype=float)
            if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 4
                    or not np.isfinite(points).all() or not np.array_equal(points[0], points[-1])
                    or (np.abs(points[:, 0]) > 180).any() or (np.abs(points[:, 1]) > 90).any()):
                raise ValueError()
        polygon = shape(data)
        if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
            raise ValueError()
        if polygon.bounds[2] - polygon.bounds[0] > 180:
            raise ValueError()  # Dateline-spanning selections require a different projection.
        return polygon
    except (ValueError, TypeError, KeyError, shapely.errors.GEOSException) as exc:
        raise LandSelectionError('land_area must be a valid, closed GeoJSON Polygon in longitude/latitude coordinates, without self-intersections.') from exc


def land_candidate_mask(polygon, dem_result, epsg):
    """Require whole candidate cells inside land; measure available terrain coverage.

    Full-cell containment also lets ranking reject natural depressions that
    straddle the land boundary. The DEM and hydrology masks are never changed.
    """
    projected = transform(Transformer.from_crs(4326, epsg, always_xy=True).transform, polygon)
    if not projected.is_valid or not np.isfinite(projected.area) or projected.area <= 0:
        raise LandSelectionError('Selected land could not be projected onto this survey.')
    dem = dem_result['dem']
    valid = np.asarray(dem_result.get('valid_mask', np.isfinite(dem)), dtype=bool) & np.isfinite(dem)
    xx, yy = np.meshgrid(dem_result['x_coords'], dem_result['y_coords'])
    half = dem_result['resolution_m'] / 2
    left, bottom, right, top = projected.bounds
    overlaps = valid & (xx+half >= left) & (xx-half <= right) & (yy+half >= bottom) & (yy-half <= top)
    cells = shapely.box(xx[overlaps]-half, yy[overlaps]-half, xx[overlaps]+half, yy[overlaps]+half)
    mask = np.zeros_like(valid)
    mask[overlaps] = shapely.covers(projected, cells)
    covered_area = float(shapely.area(shapely.intersection(projected, cells)).sum())
    if covered_area <= 0:
        raise LandSelectionError('Selected land does not overlap the available survey terrain. Draw a boundary within the contour map.')
    if not mask.any():
        raise LandSelectionError('Selected land contains no complete terrain cells. Enlarge the boundary or use a finer contour survey.')
    fraction = min(1., covered_area / projected.area)
    return mask, {'geometry': mapping(polygon), 'area_ha': projected.area / 10000,
                  'terrain_coverage_fraction': fraction, 'partial_terrain_coverage': fraction < 1 - 1e-6,
                  'candidate_cell_count': int(mask.sum()), 'containment': 'whole_terrain_cells'}
