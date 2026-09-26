"""Exact cell-edge polygons, including holes and disconnected raster parts."""
import numpy as np
from shapely.geometry import box, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer


def mask_geometry(mask, dem_result, epsg):
    res = dem_result['resolution_m']
    xs, ys = dem_result['x_coords'], dem_result['y_coords']
    rectangles = []
    for row in np.flatnonzero(mask.any(axis=1)):
        changes = np.diff(np.pad(mask[row].astype(np.int8), 1))
        for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
            rectangles.append(box(xs[start]-res/2, ys[row]-res/2,
                                  xs[end-1]+res/2, ys[row]+res/2))
    geom = unary_union(rectangles)
    project = Transformer.from_crs(epsg, 4326, always_xy=True)
    geographic = transform(project.transform, geom)
    parts = list(geographic.geoms) if geographic.geom_type == 'MultiPolygon' else [geographic]
    outline = list(max(parts, key=lambda p: p.area).exterior.coords) if rectangles else []
    return {'geometry': mapping(geographic), 'polygon': [list(p) for p in outline]}
