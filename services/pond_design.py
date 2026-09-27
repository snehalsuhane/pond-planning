"""Illustrative excavated-pond geometry and land/water checks, independent of routing."""
import math
import logging
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.affinity import rotate, translate
from shapely.ops import transform

from utils.land_selection import parse_land_area
from utils.projection import get_utm_epsg
from services.waterways import screen_waterways, WaterwayDataError


def number(data, key, default, minimum, maximum):
    value = data.get(key, default)
    try:
        if isinstance(value, bool):
            raise ValueError
        value = float(value)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f'{key} must be a number from {minimum} to {maximum}.') from exc
    return value


def design_pond(data, water_buffer_m=30.):
    """Dimensions are excavation rim dimensions; depth includes freeboard.

    This assumes level ground and uniform side slopes, not surveyed earthworks.
    Depth bounds are demo limits, not a determination of safe excavation depth.
    """
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object with site, land_area and pond dimensions.')
    land = parse_land_area(data.get('land_area'))
    site = data.get('site')
    if not isinstance(site, dict):
        raise ValueError('site must contain latitude and longitude.')
    lat = number(site, 'latitude', None, -80, 84)
    lon = number(site, 'longitude', None, -180, 180)
    if (land.bounds[1] < -80 or land.bounds[3] > 84
            or max(abs(land.bounds[0]-lon), abs(land.bounds[2]-lon),
                   abs(land.bounds[1]-lat), abs(land.bounds[3]-lat)) > 3):
        raise ValueError('Choose a local land boundary near the selected site, within the supported latitude range.')
    length = number(data, 'length_m', 40, 5, 500)
    width = number(data, 'width_m', 30, 5, 500)
    depth = number(data, 'depth_m', 2, .5, 3)
    bearing = number(data, 'orientation_deg', 0, 0, 360)
    slope = number(data, 'side_slope', 2, 1, 4)
    freeboard = number(data, 'freeboard_m', .5, .1, 1)
    margin = number(data, 'margin_m', 5, 1, 30)
    bottom_length, bottom_width = length - 2*slope*depth, width - 2*slope*depth
    if freeboard >= depth:
        raise ValueError('Freeboard must be less than excavation depth.')
    if min(bottom_length, bottom_width) <= 0:
        raise ValueError('Dimensions are too small for this depth and side slope: the bottom must have positive length and width.')
    epsg = get_utm_epsg(lon, lat)
    project = Transformer.from_crs(4326, epsg, always_xy=True)
    unproject = Transformer.from_crs(epsg, 4326, always_xy=True)
    x, y = project.transform(lon, lat)
    def rectangle(l, w):
        # Length points north at 0°, and east at 90° (clockwise grid bearing).
        return translate(rotate(box(-w/2, -l/2, w/2, l/2), -bearing, origin=(0, 0)), x, y)
    footprint = rectangle(length, width)
    clearance = footprint.buffer(margin, join_style=2)
    water_length, water_width = length - 2*slope*freeboard, width - 2*slope*freeboard
    water_depth = depth - freeboard
    bottom_area = bottom_length * bottom_width
    mid_area = (bottom_length + slope*water_depth) * (bottom_width + slope*water_depth)
    water_area = water_length * water_width
    capacity = water_depth/6 * (bottom_area + 4*mid_area + water_area)
    land_utm = transform(project.transform, land)
    if not land_utm.is_valid or not math.isfinite(land_utm.area):
        raise ValueError('Selected land could not be projected around this site.')
    inside = land_utm.covers(clearance)
    geo = lambda geometry: mapping(transform(unproject.transform, geometry))
    result = {
        'status': 'success', 'screening_status': 'unverified',
        'dimensions': {'length_m': length, 'width_m': width, 'depth_m': depth,
                       'orientation_deg': bearing, 'side_slope': slope,
                       'freeboard_m': freeboard, 'margin_m': margin},
        'capacity_m3': capacity, 'water_depth_m': water_depth,
        'bottom_length_m': bottom_length, 'bottom_width_m': bottom_width,
        'footprint_area_m2': footprint.area, 'land_required_m2': clearance.area,
        'footprint': geo(footprint), 'clearance': geo(clearance),
        'water_surface': geo(rectangle(water_length, water_width)),
        'checks': {'within_selected_land': inside, 'avoids_mapped_water': None},
        'messages': [],
        'assumptions': 'Level ground, flat bottom and uniform side slopes. Capacity excludes freeboard. Depth limit of 3 m and other input limits are project assumptions, not construction recommendations. Ground stability, earthworks on sloping ground, inlet/outlet design and actual stored water are not assessed.',
        'formula': 'water_depth / 6 * (bottom_area + 4 * mid_water_depth_area + water_surface_area)',
    }
    if not inside:
        result['screening_status'] = 'does_not_fit'
        result['messages'].append('The excavation and its margin do not fit entirely inside the selected land. Reduce dimensions or choose another site.')
        # No provider request needed to reject a footprint that already fails.
        return result
    left, bottom, right, top = clearance.bounds
    # Geometry-only screening: two bounding coordinates and zero cell padding.
    # Intersect the complete footprint, not just its corners or raster centres.
    bounds = {'x_coords': np.array([left, right]), 'y_coords': np.array([bottom, top]), 'resolution_m': 0.}
    try:
        _, metadata = screen_waterways(bounds, epsg, buffer_m=water_buffer_m, include_geometry=True)
        excluded = transform(project.transform, shape(metadata.pop('exclusion_geometry')))
        clear = not clearance.intersects(excluded)
        result['checks']['avoids_mapped_water'] = clear
        result['waterway_screening'] = metadata
        result['screening_status'] = 'passes_checks' if clear else 'does_not_fit'
        result['messages'].append('The footprint and margin fit inside the land and avoid mapped-water buffers.' if clear
                                  else 'The footprint or margin intersects a mapped-water buffer. Reduce dimensions, change orientation, or choose another site.')
    except WaterwayDataError as exc:
        logging.getLogger(__name__).warning('Pond footprint screening: %s', exc)
        result['messages'].append('Water screening is unavailable. Geometry and capacity are shown, but this design has not passed the map checks. Retry the design check.')
    return result
