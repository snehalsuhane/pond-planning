"""Fetch bounded Copernicus GLO-30 windows and adapt them to our terrain grid."""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
from zipfile import BadZipFile

import numpy as np
from pyproj import Transformer
from shapely.ops import transform as transform_geometry

from utils.projection import get_utm_epsg

SOURCE = 'Copernicus GLO-30'
SOURCE_URL = 'https://copernicus-dem-30m.s3.amazonaws.com/readme.html'
RESOLUTION_M = 30.


class ElevationDataError(ValueError):
    """Public terrain is missing, incomplete, or temporarily unavailable."""


class ElevationSelectionError(ValueError):
    """Land selection exceeds the supported extent of a single analysis."""


def make_grid(land, buffer_m=2000., max_cells=1_000_000):
    """Snap a metric analysis grid around the selected polygon, not its sample."""
    if land.bounds[1] < -80 or land.bounds[3] > 84 or land.bounds[2]-land.bounds[0] > 6:
        raise ElevationSelectionError('Choose a local land area between 80°S and 84°N, away from the date line.')
    epsg = get_utm_epsg(land.centroid.x, land.centroid.y)
    project = Transformer.from_crs(4326, epsg, always_xy=True)
    polygon = transform_geometry(project.transform, land)
    left, bottom, right, top = polygon.bounds
    if not np.isfinite([left, bottom, right, top, polygon.area]).all():
        raise ElevationSelectionError('Selected land cannot be projected onto a local terrain grid.')
    if polygon.area > 25_000_000 or max(right-left, top-bottom) > 15000:
        raise ElevationSelectionError('Select at most 2,500 hectares, with no dimension longer than 15 km.')
    if not np.isfinite(buffer_m) or buffer_m < 0:
        raise ElevationSelectionError('Terrain buffer must be finite and non-negative.')
    left = math.floor((left-buffer_m)/RESOLUTION_M)*RESOLUTION_M
    bottom = math.floor((bottom-buffer_m)/RESOLUTION_M)*RESOLUTION_M
    right = math.ceil((right+buffer_m)/RESOLUTION_M)*RESOLUTION_M
    top = math.ceil((top+buffer_m)/RESOLUTION_M)*RESOLUTION_M
    width, height = round((right-left)/RESOLUTION_M), round((top-bottom)/RESOLUTION_M)
    if width*height > max_cells:
        raise ElevationSelectionError('Terrain processing limit reached. Select a smaller area.')
    return {'epsg': epsg, 'left': left, 'bottom': bottom, 'right': right, 'top': top,
            'width': width, 'height': height, 'buffer_m': float(buffer_m)}


def tile_url(latitude, longitude):
    ns, ew = ('N' if latitude >= 0 else 'S'), ('E' if longitude >= 0 else 'W')
    name = f'Copernicus_DSM_COG_10_{ns}{abs(latitude):02d}_00_{ew}{abs(longitude):03d}_00_DEM'
    return f'https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif'


def read_window(grid):
    """Read only necessary COG blocks, including neighboring tiles at seams."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, transform_bounds, Resampling
    from rasterio.windows import Window, from_bounds, transform as window_transform

    bounds = transform_bounds(grid['epsg'], 4326, grid['left'], grid['bottom'],
                              grid['right'], grid['top'], densify_pts=21)
    if bounds[0] < -180 or bounds[2] > 180 or bounds[0] >= bounds[2]:
        raise ElevationSelectionError('The surrounding terrain crosses the date line. Select another area.')
    # Pixel centers are degree-aligned; include nearby tiles for offset edges.
    pad = 3/3600
    tiles = [(lat, lon)
             for lat in range(math.floor(bounds[1]-pad), math.floor(bounds[3]+pad)+1)
             for lon in range(math.floor(bounds[0]-pad), math.floor(bounds[2]+pad)+1)
             if -90 <= lat < 90 and -180 <= lon < 180]
    if len(tiles) > 9:
        raise ElevationSelectionError('This selection spans too many terrain tiles. Select a smaller area.')
    target = from_origin(grid['left'], grid['top'], RESOLUTION_M, RESOLUTION_M)
    north_up = np.full((grid['height'], grid['width']), np.nan, dtype='float32')
    sources = []
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',
                      CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif', GDAL_HTTP_TIMEOUT='25',
                      GDAL_HTTP_CONNECTTIMEOUT='10', GDAL_HTTP_MAX_RETRY='1'):
        for lat, lon in tiles:
            url = tile_url(lat, lon)
            try:
                with rasterio.open(url) as src:
                    west, south = max(bounds[0], src.bounds.left), max(bounds[1], src.bounds.bottom)
                    east, north = min(bounds[2], src.bounds.right), min(bounds[3], src.bounds.top)
                    if west >= east or south >= north:
                        continue
                    window = from_bounds(west, south, east, north, src.transform)
                    c0, r0 = max(0, math.floor(window.col_off)-2), max(0, math.floor(window.row_off)-2)
                    c1 = min(src.width, math.ceil(window.col_off+window.width)+2)
                    r1 = min(src.height, math.ceil(window.row_off+window.height)+2)
                    window = Window(c0, r0, c1-c0, r1-r0)
                    data = src.read(1, window=window, masked=True).astype('float32').filled(np.nan)
                    part = np.full_like(north_up, np.nan)
                    reproject(data, part, src_transform=window_transform(window, src.transform),
                              src_crs=src.crs, src_nodata=np.nan, dst_transform=target,
                              dst_crs=f"EPSG:{grid['epsg']}", dst_nodata=np.nan,
                              resampling=Resampling.bilinear)
                    valid = np.isfinite(part)
                    north_up[valid] = part[valid]
                    sources.append(url)
            except rasterio.errors.RasterioError as exc:
                # Some ocean/unreleased tiles are absent. Require complete final
                # coverage below; never replace absent elevations with zero.
                if '404' in str(exc):
                    continue
                raise ElevationDataError('Could not download public elevation. Check the server connection and retry, or upload contours.') from exc
    if not np.isfinite(north_up).all():
        raise ElevationDataError('Public elevation does not fully cover this area and its surroundings. Select another area or upload a contour survey.')
    return north_up, sources


def fetch_elevation(land, buffer_m=2000., cache_dir=None):
    """Return the same DEM interface as contour interpolation, plus provenance.

    The public dataset is static. Cache complete projected windows for repeat
    analyses; a versioned key prevents reuse after adapter changes.
    """
    grid = make_grid(land, buffer_m)
    directory = Path(cache_dir or os.environ.get('TERRAIN_CACHE_DIR',
                     str(Path(__file__).resolve().parents[1]/'.cache'/'terrain')))
    key = hashlib.sha256(json.dumps({'adapter': 1, 'source': SOURCE_URL, 'grid': grid}, sort_keys=True).encode()).hexdigest()
    path = directory / f'{key}.npz'
    north_up = None
    cached = False
    try:
        with np.load(path, allow_pickle=False) as record:
            array = record['dem']
            sources = json.loads(str(record['sources']))
            if array.shape == (grid['height'], grid['width']) and np.isfinite(array).all():
                north_up, cached = array, True
    except (OSError, ValueError, KeyError, EOFError, BadZipFile):
        pass
    if north_up is None:
        north_up, sources = read_window(grid)
        temporary = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, suffix='.npz', delete=False) as output:
                temporary = Path(output.name)
                np.savez_compressed(output, dem=north_up, sources=json.dumps(sources))
            os.replace(temporary, path)
        except OSError as exc:
            logging.getLogger(__name__).warning('Could not cache public terrain: %s', exc)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    # North-up GeoTIFF rows must be reversed for the existing south-to-north grid.
    dem = np.flipud(north_up).astype(float)
    x = grid['left'] + (np.arange(grid['width'])+.5)*RESOLUTION_M
    y = grid['bottom'] + (np.arange(grid['height'])+.5)*RESOLUTION_M
    result = {'dem': dem, 'valid_mask': np.isfinite(dem), 'x_coords': x, 'y_coords': y,
              'resolution_m': RESOLUTION_M, 'shape': dem.shape, 'nan_fraction': 0.,
              'extrapolated_fraction': 0., 'elevation_min': float(dem.min()), 'elevation_max': float(dem.max()),
              'bounds': {'min_x': float(x[0]), 'min_y': float(y[0]), 'max_x': float(x[-1]), 'max_y': float(y[-1])}}
    metadata = {'name': SOURCE, 'source_url': SOURCE_URL, 'tile_urls': sources,
                'nominal_resolution_m': 30, 'model': 'surface_elevation', 'buffer_m': buffer_m,
                'cached': cached, 'coverage_note': 'Public surface elevations support preliminary terrain screening; small field features may not be resolved.'}
    return result, grid['epsg'], metadata
