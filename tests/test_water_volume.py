"""Annual runoff formula, uncertainty propagation, and graceful rainfall outages."""
from copy import deepcopy
import pytest
from services import water_volume as volume
from services.rainfall import RainfallDataError


def fake_rainfall(*args):
    return {'status': 'available', 'mean_annual_mm': 1000., 'start_year': 2016, 'end_year': 2025}


@pytest.mark.parametrize('value', [None, True, False, '', 'abc', -0.1, 1.1, float('nan'), float('inf')])
def test_invalid_coefficient(value):
    with pytest.raises(ValueError):
        volume.parse_runoff_coefficient(value)


@pytest.mark.parametrize('coefficient, expected', [(0.3, 300000.), (0., 0.), (1., 1000000.)])
def test_volume_formula_preserves_ranking(monkeypatch, coefficient, expected):
    monkeypatch.setattr(volume, 'fetch_rainfall', fake_rainfall)
    site = {'latitude': 21., 'longitude': 81., 'rank': 1, 'score': .2,
            'catchment': {'area_m2': 1000000., 'boundary_truncated': False}}
    result = {'pond_candidates': [deepcopy(site)]}
    volume.add_water_volumes(result, coefficient)
    got = result['pond_candidates'][0]
    assert got.pop('water_volume') == {'annual_m3': expected, 'unit': 'm3/year', 'status': 'estimated'}
    assert got == site


@pytest.mark.parametrize('truncated,sensitivity', [(True, 0.), (False, .8)])
def test_uncertain_catchment_means_provisional_volume(monkeypatch, truncated, sensitivity):
    monkeypatch.setattr(volume, 'fetch_rainfall', fake_rainfall)
    result = {'pond_candidates': [{'latitude': 21., 'longitude': 81.,
              'catchment': {'area_m2': 100., 'boundary_truncated': truncated},
              'assessment': {'routing_sensitivity_fraction': sensitivity}}]}
    volume.add_water_volumes(result, .3)
    assert result['pond_candidates'][0]['water_volume']['status'] == 'provisional'


def test_outage_preserves_sites_with_null_volume(monkeypatch):
    def fail(*args):
        raise RainfallDataError('unavailable')
    monkeypatch.setattr(volume, 'fetch_rainfall', fail)
    result = {'pond_candidates': [{'latitude': 21., 'longitude': 81., 'catchment': {'area_m2': 100.}}]}
    volume.add_water_volumes(result, .3)
    assert result['rainfall']['status'] == 'unavailable'
    assert result['pond_candidates'][0]['water_volume'] == {'annual_m3': None, 'unit': 'm3/year', 'status': 'unavailable'}
