"""Pond capacity geometry, full-footprint screening, and HTTP validation."""
import pytest
from pyproj import Transformer
from shapely.geometry import box, mapping, shape, Polygon
from shapely.ops import transform

from app import create_app
from services.pond_design import design_pond
from services.waterways import WaterwayDataError

TO_GEO = Transformer.from_crs(32644, 4326, always_xy=True)
TO_METRIC = Transformer.from_crs(4326, 32644, always_xy=True)
X, Y = 500000, 2300000


def geographic(geometry):
    return mapping(transform(TO_GEO.transform, geometry))


def request_data():
    lon, lat = TO_GEO.transform(X, Y)
    return {'site': {'latitude': lat, 'longitude': lon},
            'land_area': geographic(box(X-100, Y-100, X+100, Y+100))}


@pytest.fixture(autouse=True)
def water(monkeypatch):
    monkeypatch.setattr('services.waterways.fetch_waterways', lambda bounds: {'elements': []})


def test_capacity_excludes_freeboard_and_accounts_for_sloped_sides():
    result = design_pond(request_data())
    # 40x30 rim, 2m excavation, 2:1 sides, 0.5m freeboard:
    # bottom 32x22, midwater 35x25, water surface 38x28, 1.5m water depth.
    assert result['capacity_m3'] == pytest.approx(1.5/6*(32*22+4*35*25+38*28))
    assert result['water_depth_m'] == 1.5
    assert result['footprint_area_m2'] == pytest.approx(1200)
    assert result['land_required_m2'] == pytest.approx(2000)
    assert result['screening_status'] == 'passes_checks'
    assert result['checks'] == {'within_selected_land': True, 'avoids_mapped_water': True}


def test_rotation_swaps_length_axis_without_changing_capacity():
    a = design_pond(request_data())
    b = design_pond(dict(request_data(), orientation_deg=90))
    first = transform(TO_METRIC.transform, shape(a['footprint'])).bounds
    second = transform(TO_METRIC.transform, shape(b['footprint'])).bounds
    assert first[3]-first[1] == pytest.approx(40)
    assert second[2]-second[0] == pytest.approx(40)
    assert a['capacity_m3'] == b['capacity_m3']


@pytest.mark.parametrize('land', [box(X-20, Y-25, X+20, Y+24),
    Polygon(box(X-100,Y-100,X+100,Y+100).exterior.coords,
            [box(X+5,Y+5,X+10,Y+10).exterior.coords])])
def test_entire_margin_and_land_holes_checked(land, monkeypatch):
    def unexpected(*args):
        pytest.fail('Do not query water when land already fails')
    monkeypatch.setattr('services.waterways.fetch_waterways', unexpected)
    result = design_pond(dict(request_data(), land_area=geographic(land)))
    assert result['screening_status'] == 'does_not_fit'
    assert not result['checks']['within_selected_land']
    assert result['checks']['avoids_mapped_water'] is None


def test_water_crossing_interior_is_detected_even_when_corners_clear(monkeypatch):
    # Short mapped stream inside footprint, nowhere near its corners.
    coordinates = [TO_GEO.transform(X-1, Y), TO_GEO.transform(X+1, Y)]
    water = {'elements': [{'type': 'way', 'tags': {'waterway': 'stream'},
                           'geometry': [{'lon': lon, 'lat': lat} for lon, lat in coordinates]}]}
    monkeypatch.setattr('services.waterways.fetch_waterways', lambda bounds: water)
    result = design_pond(request_data(), water_buffer_m=0)
    assert result['screening_status'] == 'does_not_fit'
    assert result['checks']['avoids_mapped_water'] is False


def test_water_failure_is_unverified_not_clear(monkeypatch):
    def fail(*args):
        raise WaterwayDataError('offline')
    monkeypatch.setattr('services.waterways.fetch_waterways', fail)
    result = design_pond(request_data())
    assert result['screening_status'] == 'unverified'
    assert result['checks']['avoids_mapped_water'] is None
    assert result['capacity_m3'] > 0


@pytest.mark.parametrize('changes', [{'depth_m': 4}, {'depth_m': .5, 'freeboard_m': .5},
    {'width_m': 5}, {'side_slope': 0}, {'length_m': float('nan')}, {'width_m': True},
    {'orientation_deg': 361}, {'margin_m': 0}, {'land_area': None}, {'site': None}])
def test_invalid_design_returns_400(changes):
    client = create_app().test_client()
    response = client.post('/api/designPond', json=dict(request_data(), **changes))
    assert response.status_code == 400
    assert response.get_json()['status'] == 'error'


def test_design_route_is_independent_of_terrain_and_rainfall(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Design should not rerun terrain or rainfall')
    monkeypatch.setattr('services.area_service.fetch_elevation', unexpected)
    monkeypatch.setattr('services.water_volume.fetch_rainfall', unexpected)
    response = create_app().test_client().post('/api/designPond', json=request_data())
    assert response.status_code == 200
    assert response.get_json()['screening_status'] == 'passes_checks'
