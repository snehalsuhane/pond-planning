"""Calendar completeness, units, caching and upstream failure handling."""
from datetime import date, timedelta
import io
import json
import pytest
from services import rainfall


def history(start=2020, end=2021, daily_value=1.):
    day, stop = date(start, 1, 1), date(end+1, 1, 1)
    values = {}
    while day < stop:
        values[day.strftime('%Y%m%d')] = daily_value
        day += timedelta(days=1)
    return {'header': {'start': f'{start}0101', 'end': f'{end}1231', 'time_standard': 'UTC', 'fill_value': -999},
            'parameters': {'PRECTOTCORR': {'units': 'mm/day'}},
            'properties': {'parameter': {'PRECTOTCORR': values}}, 'messages': []}


def test_annual_totals_include_leap_day():
    result = rainfall.summarize_rainfall(history(), 2020, 2021)
    assert result['annual_totals_mm'] == {'2020': 366, '2021': 365}
    assert result['mean_annual_mm'] == 365.5


@pytest.mark.parametrize('value', [-999, -1, None, True, '1', float('nan'), float('inf')])
def test_invalid_day_is_not_zero(value):
    data = history()
    data['properties']['parameter']['PRECTOTCORR']['20200229'] = value
    with pytest.raises(rainfall.RainfallDataError):
        rainfall.summarize_rainfall(data, 2020, 2021)


def test_missing_day_and_wrong_units_rejected():
    data = history()
    del data['properties']['parameter']['PRECTOTCORR']['20200229']
    with pytest.raises(rainfall.RainfallDataError):
        rainfall.summarize_rainfall(data, 2020, 2021)
    data = history()
    data['parameters']['PRECTOTCORR']['units'] = 'mm/hour'
    with pytest.raises(rainfall.RainfallDataError):
        rainfall.summarize_rainfall(data, 2020, 2021)


def test_fetch_uses_complete_years_and_reuses_valid_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('RAINFALL_CACHE_DIR', str(tmp_path))
    calls = []
    end = date.today().year - 1
    def fetch(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(history(end-9, end)).encode())
    monkeypatch.setattr(rainfall, 'urlopen', fetch)
    first = rainfall.fetch_rainfall(21.23456, 81.23456)
    assert first == rainfall.fetch_rainfall(21.23456, 81.23456)
    assert len(calls) == 1
    assert f'start={end-9}0101' in calls[0] and f'end={end}1231' in calls[0]
    assert first['location'] == {'latitude': 21.235, 'longitude': 81.235}
    next(tmp_path.glob('*.json')).write_text('{}')
    rainfall.fetch_rainfall(21.23456, 81.23456)
    assert len(calls) == 2


def test_failed_request_not_cached(monkeypatch, tmp_path):
    monkeypatch.setenv('RAINFALL_CACHE_DIR', str(tmp_path))
    def fail(*args, **kwargs):
        raise OSError('unreachable')
    monkeypatch.setattr(rainfall, 'urlopen', fail)
    with pytest.raises(rainfall.RainfallDataError):
        rainfall.fetch_rainfall(21, 81)
    assert not list(tmp_path.iterdir())
