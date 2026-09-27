"""Map-only requests use the existing analysis with bounded surrounding terrain."""
import pytest
from shapely.geometry import box, mapping, Point, shape

from app import create_app
from services.elevation import ElevationDataError
from services.waterways import WaterwayDataError
from tests.test_pond import terrain
from tests.test_land_selection import geographic


@pytest.fixture
def public_client(monkeypatch, tmp_path):
    calls = []
    def fetch(land, buffer_m):
        calls.append(buffer_m)
        d = terrain()
        d.update(shape=d['dem'].shape, bounds={'min_x':d['x_coords'][0], 'min_y':d['y_coords'][0],
                                             'max_x':d['x_coords'][-1], 'max_y':d['y_coords'][-1]},
                 elevation_min=float(d['dem'].min()), elevation_max=float(d['dem'].max()), nan_fraction=0.)
        return d, 32644, {'name':'Copernicus GLO-30', 'buffer_m':buffer_m}
    monkeypatch.setattr('services.area_service.fetch_elevation', fetch)
    monkeypatch.setattr('services.waterways.fetch_waterways', lambda bounds: {'elements': []})
    app = create_app()
    app.config.update(TESTING=True, UPLOAD_FOLDER=str(tmp_path))
    return app.test_client(), calls


def selection():
    return geographic(box(500065, 2300065, 500835, 2300835))


def test_public_analysis_needs_no_file_and_keeps_upstream_area(public_client):
    client, calls = public_client
    polygon = selection()
    response = client.post('/api/analyzeArea', json={'land_area': mapping(polygon)})
    assert response.status_code == 200
    data = response.get_json()
    assert calls == [2000., 4000.]
    assert data['planning']['expansion_status'] == 'limit_reached'
    assert data['planning']['terrain_scope'] == 'buffered_public_dem'
    assert data['waterway_screening']['status'] == 'screened_against_mapped_water'
    assert data['dem']['resolution_m'] == 30
    assert data['terrain']['geometry']['type'] == 'Polygon'
    assert 0 < len(data['pond_candidates']) <= 5
    assert all(polygon.covers(Point(s['longitude'], s['latitude'])) for s in data['pond_candidates'])
    assert any(not polygon.covers(shape(s['catchment']['geometry'])) for s in data['pond_candidates'])
    assert all('grid_row' not in s for s in data['pond_candidates'])


@pytest.mark.parametrize('body', [None, [], {}, {'land_area': None}, {'land_area': {'type':'Point', 'coordinates':[81,21]}}])
def test_public_request_requires_valid_polygon(public_client, body):
    client, calls = public_client
    assert client.post('/api/analyzeArea', json=body).status_code == 400
    assert not calls


def test_public_download_failure_returns_503(public_client, monkeypatch):
    client, _ = public_client
    def fail(*args, **kwargs):
        raise ElevationDataError('Public elevation unavailable')
    monkeypatch.setattr('services.area_service.fetch_elevation', fail)
    response = client.post('/api/analyzeArea', json={'land_area': mapping(selection())})
    assert response.status_code == 503
    assert 'pond_candidates' not in response.get_json()


def test_public_water_failure_never_returns_unscreened_sites(public_client, monkeypatch):
    client, _ = public_client
    def fail(*args, **kwargs):
        raise WaterwayDataError('Network unavailable')
    monkeypatch.setattr('services.area_service.screen_waterways', fail)
    response = client.post('/api/analyzeArea', json={'land_area': mapping(selection())})
    assert response.status_code == 503
    assert response.get_json()['waterway_screening']['status'] == 'unavailable'
    assert 'pond_candidates' not in response.get_json()


def test_failed_expansion_retains_only_earlier_screened_result(public_client, monkeypatch):
    import services.area_service as service
    client, _ = public_client
    original = service.fetch_elevation
    def fetch(land, buffer_m):
        if buffer_m == 4000:
            raise ElevationDataError('Expansion unavailable')
        return original(land, buffer_m)
    monkeypatch.setattr(service, 'fetch_elevation', fetch)
    data = client.post('/api/analyzeArea', json={'land_area': mapping(selection())}).get_json()
    assert data['status'] == 'success'
    assert data['planning']['terrain_buffer_m'] == 2000
    assert data['planning']['expansion_status'] == 'unavailable'
    assert data['waterway_screening']['status'] == 'screened_against_mapped_water'
    assert any(s['catchment']['boundary_truncated'] for s in data['pond_candidates'])


def test_complete_catchment_does_not_trigger_expansion(public_client, monkeypatch):
    import services.area_service as service
    client, calls = public_client
    rank = service.rank_pond_candidates
    def complete(*args, **kwargs):
        result = rank(*args, **kwargs)
        for site in result['pond_candidates']:
            site['catchment']['boundary_truncated'] = False
        return result
    monkeypatch.setattr(service, 'rank_pond_candidates', complete)
    data = client.post('/api/analyzeArea', json={'land_area': mapping(selection())}).get_json()
    assert calls == [2000]
    assert data['planning']['expansion_status'] == 'not_needed'
