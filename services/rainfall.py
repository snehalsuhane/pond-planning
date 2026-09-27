"""Historical regional precipitation from NASA POWER, used as a rainfall estimate."""
from datetime import date, timedelta
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = 'https://power.larc.nasa.gov/api/temporal/daily/point'
SOURCE_URL = 'https://power.larc.nasa.gov/docs/services/api/temporal/daily/'


class RainfallDataError(ValueError):
    """Complete historical rainfall could not be obtained."""


def summarize_rainfall(data, start_year, end_year):
    """Require every calendar day, including leap days; never treat missing as zero."""
    try:
        header = data['header']
        if (data.get('messages') or header['time_standard'] != 'UTC'
                or str(header['start']) != f'{start_year}0101'
                or str(header['end']) != f'{end_year}1231'
                or data['parameters']['PRECTOTCORR']['units'] != 'mm/day'):
            raise ValueError('Unexpected precipitation metadata')
        daily = data['properties']['parameter']['PRECTOTCORR']
        annual = {}
        for year in range(start_year, end_year + 1):
            day, stop = date(year, 1, 1), date(year + 1, 1, 1)
            values = []
            while day < stop:
                value = daily[day.strftime('%Y%m%d')]
                if (isinstance(value, bool) or not isinstance(value, (float, int))
                        or not math.isfinite(value) or value < 0 or value == header.get('fill_value')):
                    raise ValueError('Missing or invalid precipitation')
                values.append(value)
                day += timedelta(days=1)
            annual[str(year)] = math.fsum(values)
        return {'status': 'available', 'source': 'NASA POWER', 'source_url': SOURCE_URL,
                'parameter': 'PRECTOTCORR', 'time_standard': 'UTC',
                'start_year': start_year, 'end_year': end_year,
                'annual_totals_mm': annual,
                'mean_annual_mm': math.fsum(annual.values()) / len(annual)}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RainfallDataError('Historical rainfall is incomplete or invalid.') from exc


def fetch_rainfall(latitude, longitude):
    """One regional point for the last ten complete years, cached for 30 days."""
    end_year = date.today().year - 1
    start_year = end_year - 9
    # Coarse regional data does not warrant survey-coordinate precision.
    latitude, longitude = round(latitude, 3), round(longitude, 3)
    params = dict(latitude=latitude, longitude=longitude, start=f'{start_year}0101',
                  end=f'{end_year}1231', parameters='PRECTOTCORR', community='AG', format='JSON')
    params['time-standard'] = 'UTC'
    url = ENDPOINT + '?' + urlencode(params)
    cache_dir = Path(os.environ.get('RAINFALL_CACHE_DIR', '.cache/rainfall'))
    cache = cache_dir / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    try:
        if time.time() - cache.stat().st_mtime < 30 * 86400:
            summary = summarize_rainfall(json.loads(cache.read_text()), start_year, end_year)
            return dict(summary, location={'latitude': latitude, 'longitude': longitude})
    except (OSError, ValueError, TypeError):
        pass
    try:
        request = Request(url, headers={'User-Agent': 'VillagePondPlanner/1.0'})
        with urlopen(request, timeout=45) as response:
            data = json.load(response)
        summary = summarize_rainfall(data, start_year, end_year)
    except (OSError, ValueError, TypeError) as exc:
        raise RainfallDataError('Historical rainfall is unavailable. Retry the analysis later.') from exc
    # Cache failure must not discard a valid provider response. Atomic replacement
    # prevents concurrent requests from seeing a partly written JSON document.
    temporary = None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=cache_dir, delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output)
        temporary.replace(cache)
    except OSError:
        logging.getLogger(__name__).warning('Could not cache historical rainfall')
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return dict(summary, location={'latitude': latitude, 'longitude': longitude})
