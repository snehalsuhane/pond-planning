"""Screen proposed sites using OSM map downloads or optional Overpass queries.

Requests are bounded and cached. A successful empty result means no mapped
water was returned, not that rivers are absent on site.
"""
import json
import hashlib
import logging
import os
from pathlib import Path
import tempfile
import time
import threading
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import numpy as np
from pyproj import Transformer
from shapely import intersects_xy
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import polygonize_full, unary_union, transform

ENDPOINT = 'https://overpass-api.de/api/interpreter'
FALLBACK_ENDPOINT = 'https://maps.mail.ru/osm/tools/overpass/api/interpreter'
THIRD_ENDPOINT = 'https://overpass.private.coffee/api/interpreter'
_ENDPOINT_FAILURES = {}
_PREFERRED_ENDPOINT = None
_CACHE = {}
_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)
_CACHE_TTL = 3600


def _complete_result(data):
    return isinstance(data, dict) and not data.get('remark') and isinstance(data.get('elements'), list)


class WaterwayDataError(ValueError):
    """Screening is unavailable; do not produce unscreened recommendations."""


def _query(bounds):
    bbox = ','.join(f'{v:.6f}' for v in bounds)
    return (f'[out:json][timeout:20];('
            f'nwr["waterway"~"^(river|stream|canal|drain|ditch|riverbank)$"]({bbox});'
            f'nwr["natural"="water"]({bbox});'
            f'nwr["landuse"="reservoir"]({bbox}););out geom;')


def fetch_overpass_waterways(bounds, endpoint=None, timeout=30):
    """Fetch water geometry with three bounded attempts and a one-hour disk cache.

    Only successful, complete responses are cached. Expired data is never used
    to silently bypass an outage. OVERPASS_ENDPOINT supports a custom instance.
    Without an override, try three distinct global services, preferring a recent
    success and moving recently failed services last for five minutes. Fresh
    cached queries covering the full requested bounds can also be reused.
    """
    global _PREFERRED_ENDPOINT
    configured_endpoint = endpoint or os.environ.get('OVERPASS_ENDPOINT')
    endpoint = configured_endpoint or ENDPOINT
    with _LOCK:
        defaults = [ENDPOINT, FALLBACK_ENDPOINT, THIRD_ENDPOINT]
        defaults.sort(key=lambda url: (_ENDPOINT_FAILURES.get(url, 0) > time.time(), url != _PREFERRED_ENDPOINT))
    endpoints = [endpoint] * 3 if configured_endpoint else defaults
    # Match the actual six-decimal query bounds when testing cached coverage.
    bounds = tuple(float(f'{v:.6f}') for v in bounds)
    query = _query(bounds)
    key = (endpoint, query)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and 0 <= now - cached[0] < _CACHE_TTL:
            return cached[1]
    directory = Path(os.environ.get('WATERWAY_CACHE_DIR',
                     str(Path(__file__).resolve().parents[1] / '.cache' / 'waterways')))
    cache_path = directory / (hashlib.sha256(repr(key).encode()).hexdigest() + '.json')
    try:
        record = json.loads(cache_path.read_text())
        age = now - float(record['fetched_at'])
        if (record['endpoint'] == endpoint and record['query'] == query
                and 0 <= age < _CACHE_TTL and _complete_result(record['data'])):
            with _LOCK:
                _CACHE[key] = (float(record['fetched_at']), record['data'])
            return record['data']
    except (OSError, ValueError, KeyError, TypeError):
        pass  # Missing, expired or corrupt cache must cause a fresh lookup.
    # A previous terrain query may already cover a moved selection or a pond
    # footprint. Reuse only matching query semantics, full coverage and fresh data.
    try:
        paths = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:128]
    except OSError:
        paths = []
    for path in paths:
        try:
            record = json.loads(path.read_text())
            outer = record['bounds']
            age = now - float(record['fetched_at'])
            if (record['endpoint'] == endpoint and len(outer) == 4
                    and record['query'] == _query(outer)
                    and outer[0] <= bounds[0] and outer[1] <= bounds[1]
                    and outer[2] >= bounds[2] and outer[3] >= bounds[3]
                    and 0 <= age < _CACHE_TTL and _complete_result(record['data'])):
                with _LOCK:
                    if len(_CACHE) >= 64:
                        _CACHE.pop(next(iter(_CACHE)))
                    _CACHE[key] = (float(record['fetched_at']), record['data'])
                return record['data']
        except (OSError, ValueError, TypeError, KeyError):
            continue
    for attempt, request_endpoint in enumerate(endpoints):
        request = Request(request_endpoint, data=urlencode({'data': query}).encode(),
                          headers={'User-Agent': 'PondPlanning/1.0',
                                   'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.load(response)
            if not _complete_result(data):
                raise WaterwayDataError('Waterway service returned an incomplete result.')
            if not configured_endpoint:
                with _LOCK:
                    _PREFERRED_ENDPOINT = request_endpoint
                    _ENDPOINT_FAILURES.pop(request_endpoint, None)
            break
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            if not configured_endpoint:
                with _LOCK:
                    _ENDPOINT_FAILURES[request_endpoint] = time.time() + 300
            retryable = not isinstance(exc, HTTPError) or exc.code in {408, 429, 500, 502, 503, 504}
            if not retryable or attempt == 2:
                raise WaterwayDataError(
                    f'Waterway lookup failed after {attempt + 1} attempt(s): {exc}. '
                    'Water screening is unavailable; retry later or set OVERPASS_ENDPOINT '
                    'to another suitable Overpass instance. No unscreened sites were selected.'
                ) from exc
            _LOG.warning('Waterway lookup at %s failed (%s); next attempt at %s (%s/3).',
                         request_endpoint, exc, endpoints[attempt + 1], attempt + 2)
            time.sleep(2**attempt)
    fetched_at = time.time()
    with _LOCK:
        if len(_CACHE) >= 64:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (fetched_at, data)
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as output:
            temporary = Path(output.name)
            json.dump({'endpoint': endpoint, 'served_by': request_endpoint,
                       'query': query, 'bounds': bounds, 'fetched_at': fetched_at, 'data': data}, output)
        os.replace(temporary, cache_path)
    except OSError as exc:
        _LOG.warning('Could not persist waterway cache: %s', exc)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return data


def fetch_waterways(bounds, endpoint=None, timeout=30):
    """Use bounded OSM map downloads by default; Overpass remains opt-in."""
    provider = os.environ.get('WATERWAY_PROVIDER', 'osm').lower()
    if endpoint is not None or provider == 'overpass':
        return fetch_overpass_waterways(bounds, endpoint=endpoint, timeout=timeout)
    if provider != 'osm':
        raise WaterwayDataError('WATERWAY_PROVIDER must be osm or overpass.')
    from services.osm_water import fetch_osm_water
    return fetch_osm_water(bounds, timeout=timeout)


def _water_geometry(element, transformer):
    def line(points):
        if not points or len(points) < 2:
            raise WaterwayDataError('Mapped water has missing geometry.')
        try:
            x, y = transformer.transform([p['lon'] for p in points], [p['lat'] for p in points])
            if not (np.isfinite(x).all() and np.isfinite(y).all()):
                raise ValueError('Non-finite coordinates')
            return LineString(zip(x, y))
        except (KeyError, TypeError, ValueError) as exc:
            raise WaterwayDataError('Mapped water has invalid geometry.') from exc

    tags = element.get('tags', {})
    area = tags.get('natural') == 'water' or tags.get('waterway') == 'riverbank' or tags.get('landuse') == 'reservoir'
    if element['type'] == 'way':
        geometry = line(element.get('geometry'))
        if area:
            if not geometry.is_ring:
                raise WaterwayDataError('Mapped water area is not a closed polygon.')
            geometry = Polygon(geometry)
        return geometry
    if element['type'] == 'relation':
        members = element.get('members', [])
        if not area:
            return unary_union([line(m.get('geometry')) for m in members if m['type'] == 'way'])
        outer = [line(m.get('geometry')) for m in members
                 if m['type'] == 'way' and m.get('role', '') in ('outer', '')]
        inner = [line(m.get('geometry')) for m in members
                 if m['type'] == 'way' and m.get('role') == 'inner']
        assembled, cuts, dangles, invalid = polygonize_full(unary_union(outer))
        if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
            raise WaterwayDataError('Mapped water relation has incomplete outer rings.')
        polygons = list(assembled.geoms)
        if not polygons:
            raise WaterwayDataError('Mapped water relation has incomplete outer rings.')
        geometry = unary_union(polygons)
        if inner:
            assembled, cuts, dangles, invalid = polygonize_full(unary_union(inner))
            if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
                raise WaterwayDataError('Mapped water relation has incomplete inner rings.')
            holes = list(assembled.geoms)
            if not holes:
                raise WaterwayDataError('Mapped water relation has incomplete inner rings.')
            geometry = geometry.difference(unary_union(holes))
        return geometry
    # Nodes do not describe a river footprint. Do not silently certify them.
    raise WaterwayDataError('Mapped water feature lacks a usable line or polygon.')


def screen_waterways(dem_result, epsg, buffer_m=30.0, provider=None, include_geometry=False):
    """Return a UTM-buffered exclusion mask and data provenance.

    For centrelines, add half the numeric mapped width when available. The
    buffer is a configurable planning assumption, not a statutory setback.
    Cell half-diagonals ensure cells intersecting the buffer are excluded.
    include_geometry exposes the same exclusion geometry in WGS84 for full
    footprint checks; geometry-only callers use resolution_m=0 (no cell padding).
    """
    if not np.isfinite(buffer_m) or buffer_m < 0:
        raise ValueError('Waterway buffer must be finite and non-negative')
    gx, gy = dem_result['x_coords'], dem_result['y_coords']
    to_geo = Transformer.from_crs(epsg, 4326, always_xy=True)
    # Query beyond the raster so nearby water can intersect the setback.
    margin = max(250.0, buffer_m + 100.0)
    lon, lat = to_geo.transform([gx[0]-margin, gx[-1]+margin, gx[0]-margin, gx[-1]+margin],
                                [gy[0]-margin, gy[0]-margin, gy[-1]+margin, gy[-1]+margin])
    data = (provider or fetch_waterways)((min(lat), min(lon), max(lat), max(lon)))
    if not isinstance(data, dict) or data.get('remark') or not isinstance(data.get('elements'), list):
        raise WaterwayDataError('Waterway data is incomplete.')
    project = Transformer.from_crs(4326, epsg, always_xy=True)
    geometries = []
    half_cell = dem_result['resolution_m'] / np.sqrt(2)
    for element in data['elements']:
        geometry = _water_geometry(element, project)
        if geometry.is_empty or not geometry.is_valid:
            raise WaterwayDataError('Mapped water geometry is invalid.')
        width = 0.0
        if geometry.geom_type in ('LineString', 'MultiLineString'):
            try:
                width = float(str(element.get('tags', {}).get('width', '0')).removesuffix(' m'))
                if not np.isfinite(width) or width < 0:
                    width = 0.0
            except ValueError:
                pass
        distance = buffer_m + width / 2 + half_cell
        geometries.append(geometry.buffer(distance) if distance else geometry)
    xx, yy = np.meshgrid(gx, gy)
    mask = intersects_xy(unary_union(geometries), xx, yy) if geometries else np.zeros(xx.shape, dtype=bool)
    metadata = {
        'status': 'screened_against_mapped_water',
        'source': data.get('source', 'OpenStreetMap via Overpass API'),
        'attribution': '© OpenStreetMap contributors',
        'source_url': 'https://www.openstreetmap.org/copyright',
        'feature_count': len(geometries), 'buffer_m': float(buffer_m),
        'excluded_cell_count': int(mask.sum()),
        'data_timestamp': data.get('osm3s', {}).get('timestamp_osm_base'),
        'coverage_note': data.get('coverage_note', 'Unmapped or outdated rivers may be absent; verify against survey or imagery.'),
        'retrieved_at': data.get('retrieved_at'),
    }

    if include_geometry:
        metadata['exclusion_geometry'] = mapping(transform(to_geo.transform, unary_union(geometries)))
    return mask, metadata
