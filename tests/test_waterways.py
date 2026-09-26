import numpy as np
import pytest
from pyproj import Transformer
from services.waterways import screen_waterways, WaterwayDataError, fetch_waterways


@pytest.fixture(autouse=True)
def isolated_water_cache(monkeypatch, tmp_path):
    import services.waterways as waterways
    waterways._CACHE.clear()
    monkeypatch.setenv('WATERWAY_CACHE_DIR', str(tmp_path / 'water-cache'))
    monkeypatch.setattr(waterways.time, 'sleep', lambda _: None)
    yield
    waterways._CACHE.clear()


def dem():
    return {'x_coords': 500000 + np.arange(21)*10., 'y_coords': 2300000 + np.arange(21)*10.,
            'resolution_m': 10.}


def points(coords):
    t = Transformer.from_crs(32644, 4326, always_xy=True)
    return [dict(zip(('lon', 'lat'), t.transform(500000+x, 2300000+y))) for x, y in coords]


def test_river_buffer_includes_width_and_cell_extent():
    data = {'elements': [{'type': 'way', 'tags': {'waterway': 'river', 'width': '40'},
                          'geometry': points([(100, -100), (100, 300)])}]}
    mask, metadata = screen_waterways(dem(), 32644, 10, provider=lambda _: data)
    assert mask[10, 10] and mask[10, 13]
    assert not mask[10, 15]
    assert metadata['feature_count'] == 1


def test_water_polygon_interior_is_excluded():
    data = {'elements': [{'type': 'way', 'tags': {'natural': 'water'},
                          'geometry': points([(40,40),(160,40),(160,160),(40,160),(40,40)])}]}
    mask, _ = screen_waterways(dem(), 32644, 0, provider=lambda _: data)
    assert mask[10, 10]
    assert not mask[0, 0]


def test_relation_joined_outer_and_island():
    data = {'elements': [{'type': 'relation', 'tags': {'natural': 'water'}, 'members': [
        {'type': 'way', 'role': 'outer', 'geometry': points([(0,0),(200,0),(200,200)])},
        {'type': 'way', 'role': 'outer', 'geometry': points([(200,200),(0,200),(0,0)])},
        {'type': 'way', 'role': 'inner', 'geometry': points([(60,60),(140,60),(140,140),(60,140),(60,60)])},
    ]}]}
    mask, _ = screen_waterways(dem(), 32644, 0, provider=lambda _: data)
    assert mask[3, 3]
    assert not mask[10, 10]


def test_empty_coverage_is_explicit():
    mask, metadata = screen_waterways(dem(), 32644, provider=lambda _: {'elements': []})
    assert not mask.any()
    assert metadata['feature_count'] == 0
    assert 'Unmapped' in metadata['coverage_note']


@pytest.mark.parametrize('data', [{'elements': [], 'remark': 'timeout'}, {'elements': [
    {'type': 'way', 'tags': {'natural': 'water'}, 'geometry': points([(0,0),(100,0)])}]}])
def test_incomplete_data_rejected(data):
    with pytest.raises(WaterwayDataError):
        screen_waterways(dem(), 32644, provider=lambda _: data)


def test_network_failure_is_not_empty_data(monkeypatch):
    from urllib.error import URLError
    def fail(*args, **kwargs):
        raise URLError('unavailable')
    monkeypatch.setattr('services.waterways.urlopen', fail)
    with pytest.raises(WaterwayDataError, match='lookup failed'):
        fetch_waterways((0, 0, 1, 1))


def test_gateway_timeout_retries_then_reuses_disk_cache(monkeypatch):
    import io
    import services.waterways as waterways
    from urllib.error import HTTPError
    calls = []
    def fetch(request, **kwargs):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 504, 'Gateway Timeout', {}, None)
        return io.BytesIO(b'{"elements": []}')
    monkeypatch.setattr(waterways, 'urlopen', fetch)
    assert fetch_waterways((0, 0, 1, 1)) == {'elements': []}
    assert len(calls) == 2
    waterways._CACHE.clear()  # Emulate a new script process.
    assert fetch_waterways((0, 0, 1, 1)) == {'elements': []}
    assert len(calls) == 2
    fetch_waterways((0, 0, 2, 2))  # Different survey cannot reuse this response.
    assert len(calls) == 3


def test_expired_cache_does_not_bypass_service_outage(monkeypatch):
    import io
    import services.waterways as waterways
    monkeypatch.setattr(waterways, 'urlopen', lambda *a, **kw: io.BytesIO(b'{"elements": []}'))
    monkeypatch.setattr(waterways.time, 'time', lambda: 10000.)
    fetch_waterways((0, 0, 1, 1))
    waterways._CACHE.clear()
    monkeypatch.setattr(waterways.time, 'time', lambda: 14000.)
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise TimeoutError('unavailable')
    monkeypatch.setattr(waterways, 'urlopen', fail)
    with pytest.raises(WaterwayDataError, match='after 3 attempt'):
        fetch_waterways((0, 0, 1, 1))
    assert len(calls) == 3


def test_permanent_http_error_is_not_retried(monkeypatch):
    import services.waterways as waterways
    from urllib.error import HTTPError
    calls = []
    def fail(request, **kwargs):
        calls.append(1)
        raise HTTPError(request.full_url, 400, 'Bad Request', {}, None)
    monkeypatch.setattr(waterways, 'urlopen', fail)
    with pytest.raises(WaterwayDataError, match='after 1 attempt'):
        fetch_waterways((0, 0, 1, 1))
    assert len(calls) == 1


def test_incomplete_results_are_never_cached(monkeypatch, tmp_path):
    import io
    import services.waterways as waterways
    monkeypatch.setattr(waterways, 'urlopen', lambda *a, **kw:
                        io.BytesIO(b'{"elements": [], "remark": "runtime timeout"}'))
    with pytest.raises(WaterwayDataError, match='incomplete'):
        fetch_waterways((0, 0, 1, 1))
    assert not waterways._CACHE
    assert not list(tmp_path.rglob('*.json'))
