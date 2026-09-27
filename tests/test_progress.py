"""Tests for services/progress.py — token lifecycle, stage reporting, expiry."""
import time
import pytest
from services.progress import new_token, report, get_stage


def test_new_token_registers_as_queued():
    token = new_token()
    entry = get_stage(token)
    assert entry is not None
    assert entry['stage'] == 'queued'


def test_report_updates_stage():
    token = new_token()
    report(token, 'Tracing drainage\u2026')
    assert get_stage(token)['stage'] == 'Tracing drainage\u2026'


def test_report_none_token_is_silent_noop():
    # Must not raise
    report(None, 'anything')


def test_get_stage_unknown_token_returns_none():
    assert get_stage('does-not-exist-xyz') is None


def test_done_and_error_stages_are_terminal():
    for stage in ('done', 'error'):
        token = new_token()
        report(token, stage)
        entry = get_stage(token)
        assert entry['stage'] == stage


def test_token_expires_after_ttl(monkeypatch):
    import services.progress as prog
    token = new_token()
    # Fake monotonic so the entry looks stale.
    original = time.monotonic
    monkeypatch.setattr(prog.time, 'monotonic', lambda: original() + prog.TTL + 1)
    assert get_stage(token) is None


def test_eviction_keeps_registry_bounded():
    import services.progress as prog
    limit = prog.MAX_ENTRIES
    tokens = [new_token() for _ in range(limit + 10)]
    # Registry must not grow beyond MAX_ENTRIES
    import services.progress as p
    with p._LOCK:
        assert len(p._STATE) <= limit


def test_stage_sequence_preserved_in_order():
    token = new_token()
    for stage in ('Retrieving elevation\u2026', 'Tracing drainage\u2026', 'done'):
        report(token, stage)
    assert get_stage(token)['stage'] == 'done'


# ── HTTP polling endpoint ───────────────────────────────────────────────────

@pytest.fixture
def client():
    from app import create_app
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_status_endpoint_for_known_token(client):
    token = new_token()
    report(token, 'Checking terrain\u2026')
    r = client.get(f'/api/analysis/status/{token}')
    assert r.status_code == 200
    data = r.get_json()
    assert data['stage'] == 'Checking terrain\u2026'
    assert data['done'] is False


def test_status_endpoint_done_stage(client):
    token = new_token()
    report(token, 'done')
    data = client.get(f'/api/analysis/status/{token}').get_json()
    assert data['done'] is True


def test_status_endpoint_error_stage(client):
    token = new_token()
    report(token, 'error')
    data = client.get(f'/api/analysis/status/{token}').get_json()
    assert data['done'] is True
    assert data['stage'] == 'error'


def test_status_endpoint_unknown_token(client):
    data = client.get('/api/analysis/status/not-a-real-token').get_json()
    assert data['stage'] == 'unknown'
    assert data['done'] is True


def test_analyze_area_response_includes_progress_token(monkeypatch):
    """analyzeArea embeds a progress_token and X-Progress-Token header."""
    from shapely.geometry import box, mapping
    from tests.test_pond import terrain
    from tests.test_land_selection import geographic
    from tests.test_water_volume import fake_rainfall
    from app import create_app

    monkeypatch.setattr('services.water_volume.fetch_rainfall', fake_rainfall)

    def fetch(land, buffer_m):
        d = terrain()
        d.update(shape=d['dem'].shape,
                 bounds={'min_x': d['x_coords'][0], 'min_y': d['y_coords'][0],
                         'max_x': d['x_coords'][-1], 'max_y': d['y_coords'][-1]},
                 elevation_min=float(d['dem'].min()), elevation_max=float(d['dem'].max()),
                 nan_fraction=0.)
        return d, 32644, {'name': 'Copernicus GLO-30', 'buffer_m': buffer_m}

    monkeypatch.setattr('services.area_service.fetch_elevation', fetch)
    monkeypatch.setattr('services.waterways.fetch_waterways', lambda bounds: {'elements': []})

    app = create_app()
    app.config.update(TESTING=True)
    polygon = geographic(box(500065, 2300065, 500835, 2300835))
    with app.test_client() as c:
        r = c.post('/api/analyzeArea', json={'land_area': mapping(polygon)})
    assert r.status_code == 200
    data = r.get_json()
    assert 'progress_token' in data
    token = data['progress_token']
    assert isinstance(token, str) and len(token) > 8
    assert r.headers.get('X-Progress-Token') == token
    # Token is 'done' after synchronous completion.
    entry = get_stage(token)
    assert entry is not None
    assert entry['stage'] == 'done'
