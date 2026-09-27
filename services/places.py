"""User-submitted place searches, cached and rate-limited for Nominatim."""
import json
import math
import os
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_LOCK = threading.Lock()
_CACHE = {}
_LAST_REQUEST = 0.


class PlaceSearchError(ValueError):
    """The geocoding service could not supply usable results."""


def search_places(query):
    """Up to five results; no autocomplete or background geocoding requests.

    The app runs as one Flask process: serialize outgoing requests, with at
    least one second between starts. Cache repeated queries for 24 hours.
    """
    global _LAST_REQUEST
    query = ' '.join(query.split())
    if not 2 <= len(query) <= 200:
        raise ValueError('Enter a place name between 2 and 200 characters.')
    endpoint = os.environ.get('NOMINATIM_ENDPOINT', 'https://nominatim.openstreetmap.org/search')
    key = endpoint, query.casefold()
    with _LOCK:
        now = time.monotonic()
        cached = _CACHE.get(key)
        if cached and now-cached[0] < 86400:
            return cached[1]
        delay = max(0., 1.05-(now-_LAST_REQUEST))
        if delay:
            time.sleep(delay)
        _LAST_REQUEST = time.monotonic()
        request = Request(endpoint + '?' + urlencode({'q':query, 'format':'jsonv2', 'limit':5}),
                          headers={'User-Agent':'VillagePondPlanner/1.0 (student terrain-planning project)',
                                   'Accept':'application/json'})
        try:
            with urlopen(request, timeout=15) as response:
                data = json.load(response)
            if not isinstance(data, list):
                raise ValueError('Invalid search response')
            results = []
            for item in data[:5]:
                lat, lon = float(item['lat']), float(item['lon'])
                label = item['display_name']
                if not isinstance(label, str) or not label or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError('Invalid place coordinates or label')
                result = {'label':label, 'latitude':lat, 'longitude':lon}
                try:
                    south, north, west, east = map(float, item.get('boundingbox', []))
                    if (all(math.isfinite(v) for v in (south, north, west, east))
                            and -90 <= south <= lat <= north <= 90 and -180 <= west <= lon <= east <= 180):
                        result['bounds'] = [[south, west], [north, east]]
                except (TypeError, ValueError):
                    pass  # A point result is usable even without a bounding box.
                results.append(result)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PlaceSearchError('Location search is temporarily unavailable. Try again, or navigate the map using coordinates.') from exc
        if len(_CACHE) >= 128:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.monotonic(), results)
        return results
