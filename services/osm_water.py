"""Bounded, cached downloads from the main OSM map API.

This is a small-area fallback for the course app, not a bulk OSM downloader.
The map endpoint's node-based selection is not a complete intersection query.
"""
import gzip
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from pyproj import Transformer
from utils.projection import get_utm_epsg
from services.waterways import WaterwayDataError, _water_geometry

API = 'https://api.openstreetmap.org/api/0.6'
TTL = 24 * 3600
_DOWNLOADS = threading.BoundedSemaphore(2)
_TAGS = {'river', 'stream', 'canal', 'drain', 'ditch', 'riverbank'}
_LOG = logging.getLogger(__name__)
COVERAGE_NOTE = ('OSM map downloads select features by mapped vertices, so crossing or enclosing water '
                 'features with no vertices in the download bounds can be absent. Unmapped or outdated '
                 'water can also be absent; verify against survey or imagery.')


def is_water(element):
    tags = {t.get('k'): t.get('v') for t in element.findall('tag')}
    return (tags.get('waterway') in _TAGS or tags.get('natural') == 'water'
            or tags.get('landuse') == 'reservoir')


def fetch_osm_water(bounds, timeout=30):
    """Return the existing water-geometry format from complete retrieved objects.

    Cache water-only geometry for a day; reuse only full bounding-box coverage.
    At most 16 sequential requests per lookup, with a 90-second time budget.
    """
    try:
        south, west, north, east = map(float, bounds)
        if not (all(math.isfinite(v) for v in (south, west, north, east))
                and -90 <= south < north <= 90 and -180 <= west < east <= 180
                and north-south <= 1 and east-west <= 1):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise WaterwayDataError('Choose a local area for the OSM water lookup.') from exc
    # Round outwards to preserve coverage at the query/cache boundary.
    bounds = (math.floor(south*1e6)/1e6, math.floor(west*1e6)/1e6,
              math.ceil(north*1e6)/1e6, math.ceil(east*1e6)/1e6)
    directory = Path(os.environ.get('WATERWAY_CACHE_DIR',
                     str(Path(__file__).resolve().parents[1]/'.cache'/'waterways'))) / 'osm-map'
    now = time.time()
    try:
        paths = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:128]
    except OSError:
        paths = []
    for path in paths:
        try:
            record = json.loads(path.read_text())
            outer = record['bounds']
            if (record['version'] == 1 and record['api'] == API and 0 <= now-record['fetched_at'] < TTL
                    and outer[0] <= bounds[0] and outer[1] <= bounds[1]
                    and outer[2] >= bounds[2] and outer[3] >= bounds[3]
                    and isinstance(record['data']['elements'], list)
                    and not record['data'].get('remark')):
                return record['data']
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            continue
    deadline = time.monotonic() + 90
    requests = 0
    nodes, ways, relations = {}, {}, {}
    timestamp = None

    def request(path):
        nonlocal requests, timestamp
        remaining = deadline-time.monotonic()
        if requests >= 16 or remaining <= 0:
            raise WaterwayDataError('OSM download limit reached. Select a smaller land area and retry.')
        requests += 1
        req = Request(API+path, headers={'User-Agent': 'VillagePondPlanner/1.0 (student terrain-planning project)',
                                        'Accept': 'application/xml', 'Accept-Encoding': 'gzip'})
        
        for attempt in range(3):
            try:
                with _DOWNLOADS:
                    with urlopen(req, timeout=min(timeout, remaining)) as response:
                        stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
                        raw = stream.read(32*1024*1024 + 1)
                break
            except HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise
            except TimeoutError:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise

        if len(raw) > 32*1024*1024:
            raise WaterwayDataError('OSM response is too large. Select a smaller area.')
        root = ET.fromstring(raw)
        if root.tag != 'osm' or root.find('error') is not None:
            raise WaterwayDataError('OSM returned incomplete map data.')
        meta = root.find('meta')
        if meta is not None:
            timestamp = meta.get('osm_base', timestamp)
        for element in root:
            target = {'node': nodes, 'way': ways, 'relation': relations}.get(element.tag)
            if target is not None:
                if element.get('id') is None:
                    raise WaterwayDataError('OSM object has no identifier.')
                target[element.get('id')] = element

    def map_area(b, level=0):
        s,w,n,e = b
        def split():
            if level >= 3:
                raise WaterwayDataError('This area has too much OSM data. Select a smaller land boundary.')
            my,mx = (s+n)/2, (w+e)/2
            for part in ((s,w,my,mx),(s,mx,my,e),(my,w,n,mx),(my,mx,n,e)):
                map_area(part, level+1)
        if (n-s)*(e-w) > .25:
            split()
            return
        try:
            request('/map?' + urlencode({'bbox': f'{w},{s},{e},{n}'}))
        except HTTPError as exc:
            detail = exc.read(2048).decode(errors='replace').lower()
            if exc.code == 400 and ('too many nodes' in detail or 'maximum bbox' in detail):
                split()
            else:
                raise

    def coordinates(way):
        coords = []
        for nd in way.findall('nd'):
            node = nodes.get(nd.get('ref'))
            if node is None:
                raise WaterwayDataError('OSM water geometry is missing referenced nodes.')
            lon, lat = float(node.get('lon')), float(node.get('lat'))
            if not math.isfinite(lon) or not math.isfinite(lat) or abs(lon)>180 or abs(lat)>90:
                raise WaterwayDataError('OSM water geometry has invalid coordinates.')
            coords.append({'lon': lon, 'lat': lat})
        if len(coords) < 2:
            raise WaterwayDataError('OSM water geometry is incomplete.')
        return coords

    try:
        map_area(bounds)
        # The map API includes relation headers without necessarily all members.
        # Fetch full geometry only for water relations, not unrelated boundaries.
        selected_relations = [r.get('id') for r in relations.values() if is_water(r)]
        for ident in selected_relations:
            relation = relations[ident]
            if any(m.get('type') == 'way' and m.get('ref') not in ways for m in relation.findall('member')):
                request('/relation/'+str(int(ident))+'/full')
        elements = []
        for ident in selected_relations:
            relation = relations[ident]
            members = []
            for member in relation.findall('member'):
                if member.get('type') == 'relation':
                    raise WaterwayDataError('Nested OSM water relations cannot be screened completely.')
                if member.get('type') != 'way':
                    continue  # Label nodes do not define a polygon boundary.
                way = ways.get(member.get('ref'))
                if way is None:
                    raise WaterwayDataError('OSM water relation has missing member ways.')
                members.append({'type': 'way', 'role': member.get('role',''), 'geometry': coordinates(way)})
            if not members:
                raise WaterwayDataError('OSM water relation has no usable geometry.')
            elements.append({'type': 'relation', 'id': int(ident),
                             'tags': {t.get('k'):t.get('v') for t in relation.findall('tag')}, 'members': members})
        for ident, way in ways.items():
            if is_water(way):
                elements.append({'type': 'way', 'id': int(ident),
                                 'tags': {t.get('k'):t.get('v') for t in way.findall('tag')},
                                 'geometry': coordinates(way)})
        project = Transformer.from_crs(4326, get_utm_epsg((west+east)/2, (south+north)/2), always_xy=True)
        for element in elements:
            geometry = _water_geometry(element, project)
            if geometry.is_empty or not geometry.is_valid:
                raise WaterwayDataError('OSM water geometry is invalid or incomplete.')
        # A water-tagged point cannot establish the footprint of a water body.
        if any(is_water(node) for node in nodes.values()):
            raise WaterwayDataError('OSM returned a water feature mapped only as a point; its extent cannot be screened.')
    except (OSError, ValueError, TypeError, ET.ParseError) as exc:
        if isinstance(exc, WaterwayDataError):
            raise
        raise WaterwayDataError('OpenStreetMap map download failed. Retry later or select a smaller area.') from exc
    data = {'elements': elements, 'osm3s': {'timestamp_osm_base': timestamp},
            'source': 'OpenStreetMap map API', 'coverage_note': COVERAGE_NOTE,
            'retrieved_at': now, 'download_bounds': list(bounds)}
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(json.dumps(bounds).encode()).hexdigest()
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as output:
            temporary = Path(output.name)
            json.dump({'version': 1, 'api': API, 'bounds': bounds, 'fetched_at': now, 'data': data}, output)
        temporary.replace(directory/(key+'.json'))
    except OSError:
        _LOG.warning('Could not cache OSM water data')
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return data
