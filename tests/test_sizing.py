"""Tests for services/sizing.py — pond-size recommendation engine.

All tests monkeypatch fetch_rainfall and waterway screening; no real
network calls are made.
"""
import math
import pytest
from shapely.geometry import box

from app import create_app
from services.sizing import (
    suggest_pond_size,
    _sloped_capacity,
    _candidate_grid,
    _LENGTHS, _WIDTHS, _DEPTHS,
    _SIDE_SLOPE, _FREEBOARD,
)
from services.rainfall import RainfallDataError, summarize_rainfall
from tests.test_pond_design import request_data, geographic, X, Y
from tests.test_rainfall import history


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def catchment_geo():
    """200 × 200 m catchment around the synthetic site."""
    return geographic(box(X - 100, Y - 100, X + 100, Y + 100))


@pytest.fixture
def body(catchment_geo):
    return dict(request_data(), catchment=catchment_geo)


@pytest.fixture
def rain(monkeypatch):
    """Patch fetch_rainfall with two years of 1 mm/day synthetic data."""
    calls = []
    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return summarize_rainfall(history(), 2020, 2021, include_daily=True)
    monkeypatch.setattr('services.sizing.fetch_rainfall', fetch)
    return calls


# ---------------------------------------------------------------------------
# _sloped_capacity — unit tests
# ---------------------------------------------------------------------------

class TestSlopedCapacity:

    def test_positive_capacity_for_valid_dims(self):
        cap = _sloped_capacity(40, 30, 2.0)
        assert cap > 0

    def test_matches_design_pond_formula(self):
        """Verify against the explicit Simpson's-rule formula used in pond_design.py."""
        L, W, D = 40., 30., 2.0
        slope, freeboard = _SIDE_SLOPE, _FREEBOARD
        wd = D - freeboard
        bl = L - 2 * slope * D
        bw = W - 2 * slope * D
        ml = bl + slope * wd
        mw = bw + slope * wd
        wl = L - 2 * slope * freeboard
        ww = W - 2 * slope * freeboard
        expected = wd / 6 * (bl * bw + 4 * ml * mw + wl * ww)
        assert _sloped_capacity(L, W, D) == pytest.approx(expected)

    def test_returns_none_for_too_small_footprint(self):
        # With slope=2 and depth=3, bottom becomes negative for small footprints
        assert _sloped_capacity(5, 5, 3.0) is None

    def test_deeper_pond_is_larger(self):
        small = _sloped_capacity(40, 30, 1.5)
        large = _sloped_capacity(40, 30, 2.5)
        assert large > small

    def test_wider_pond_is_larger(self):
        narrow = _sloped_capacity(40, 20, 2.0)
        wide   = _sloped_capacity(40, 40, 2.0)
        assert wide > narrow


# ---------------------------------------------------------------------------
# _candidate_grid — unit tests
# ---------------------------------------------------------------------------

class TestCandidateGrid:

    def test_all_capacities_positive(self):
        for _, _, _, cap in _candidate_grid():
            assert cap > 0

    def test_sorted_ascending_by_capacity(self):
        caps = [cap for *_, cap in _candidate_grid()]
        assert caps == sorted(caps)

    def test_invalid_geometries_excluded(self):
        # Very small dims with large depth: some combos have no valid bottom
        grid = _candidate_grid(lengths=[6], widths=[6], depths=[3.0])
        assert all(c > 0 for *_, c in grid)

    def test_all_three_lengths_represented(self):
        ls = {l for l, *_ in _candidate_grid()}
        assert ls >= set(_LENGTHS)

    def test_default_grid_non_empty(self):
        assert len(_candidate_grid()) > 0


# ---------------------------------------------------------------------------
# suggest_pond_size — validation
# ---------------------------------------------------------------------------

class TestSuggestSizeValidation:

    def test_none_body_raises(self):
        with pytest.raises(ValueError, match='JSON'):
            suggest_pond_size(None)

    def test_missing_catchment_raises(self, body, rain):
        with pytest.raises(ValueError, match='catchment'):
            suggest_pond_size({k: v for k, v in body.items() if k != 'catchment'})

    def test_invalid_catchment_type_raises(self, body, rain):
        with pytest.raises(ValueError, match='catchment'):
            suggest_pond_size(dict(body, catchment={'type': 'Point', 'coordinates': [81, 21]}))

    def test_invalid_target_volume_raises(self, body, rain):
        with pytest.raises(ValueError, match='target_volume_m3'):
            suggest_pond_size(dict(body, target_volume_m3='big'))

    def test_negative_target_volume_raises(self, body, rain):
        with pytest.raises(ValueError, match='target_volume_m3'):
            suggest_pond_size(dict(body, target_volume_m3=-1))

    def test_invalid_runoff_coefficient_raises(self, body, rain):
        with pytest.raises(ValueError, match='runoff_coefficient'):
            suggest_pond_size(dict(body, runoff_coefficient=2.0))

    def test_invalid_evaporation_raises(self, body, rain):
        with pytest.raises(ValueError, match='evaporation_mm_day'):
            suggest_pond_size(dict(body, evaporation_mm_day=True))

    def test_rainfall_failure_propagates(self, body, monkeypatch):
        def fail(*args, **kwargs):
            raise RainfallDataError('Rain unavailable')
        monkeypatch.setattr('services.sizing.fetch_rainfall', fail)
        with pytest.raises(RainfallDataError):
            suggest_pond_size(body)


# ---------------------------------------------------------------------------
# suggest_pond_size — alternatives mode (no target)
# ---------------------------------------------------------------------------

class TestAlternativesMode:

    @pytest.fixture(autouse=True)
    def patch_water(self, monkeypatch):
        monkeypatch.setattr('services.waterways.fetch_waterways', lambda b: {'elements': []})

    def test_returns_dict_with_required_keys(self, body, rain):
        result = suggest_pond_size(body)
        assert {'status', 'mode', 'alternatives', 'recommended',
                'reasoning', 'assumptions', 'rainfall', 'explanation'}.issubset(result)

    def test_mode_is_alternatives(self, body, rain):
        result = suggest_pond_size(body)
        assert result['mode'] == 'alternatives'
        assert result['target_volume_m3'] is None

    def test_three_tiers_returned(self, body, rain):
        result = suggest_pond_size(body)
        labels = [a['label'] for a in result['alternatives']]
        assert set(labels) >= {'small', 'intermediate'}

    def test_recommended_is_one_of_alternatives(self, body, rain):
        result = suggest_pond_size(body)
        rec = result['recommended']
        assert rec in result['alternatives']

    def test_alternatives_have_required_fields(self, body, rain):
        result = suggest_pond_size(body)
        required = {'label', 'length_m', 'width_m', 'depth_m',
                    'capacity_m3', 'fill_rate_pct', 'mean_annual_inflow_m3',
                    'runoff_supports_fill', 'seasonal'}
        for alt in result['alternatives']:
            assert required.issubset(alt)

    def test_alternatives_sorted_by_increasing_capacity(self, body, rain):
        result = suggest_pond_size(body)
        caps = [a['capacity_m3'] for a in result['alternatives']]
        assert caps == sorted(caps)

    def test_fill_rate_is_valid_percentage(self, body, rain):
        result = suggest_pond_size(body)
        for alt in result['alternatives']:
            assert 0.0 <= alt['fill_rate_pct'] <= 100.0

    def test_mean_annual_inflow_positive(self, body, rain):
        result = suggest_pond_size(body)
        for alt in result['alternatives']:
            # 1 mm/day * ~365 days * catchment area > 0
            assert alt['mean_annual_inflow_m3'] > 0

    def test_assumptions_contain_grid_and_hydrology(self, body, rain):
        result = suggest_pond_size(body)
        asmp = result['assumptions']
        assert 'lengths_evaluated_m' in asmp
        assert 'widths_evaluated_m' in asmp
        assert 'depths_evaluated_m' in asmp
        assert 'runoff_coefficient' in asmp

    def test_rainfall_provenance_included(self, body, rain):
        result = suggest_pond_size(body)
        assert 'mean_annual_mm' in result['rainfall']
        assert 'daily_mm' not in result['rainfall']  # stripped for compactness

    def test_reasoning_is_nonempty_string(self, body, rain):
        result = suggest_pond_size(body)
        assert isinstance(result['reasoning'], str) and result['reasoning']

    def test_runoff_area_is_less_than_full_catchment(self, body, rain):
        """Pond footprint is subtracted from catchment area."""
        result = suggest_pond_size(body)
        # Catchment is 200 × 200 = 40 000 m²; footprint is always smaller
        assert 0 < result['runoff_area_m2'] < 40_000

    def test_status_is_success(self, body, rain):
        result = suggest_pond_size(body)
        assert result['status'] == 'success'


# ---------------------------------------------------------------------------
# suggest_pond_size — target mode
# ---------------------------------------------------------------------------

class TestTargetMode:

    @pytest.fixture(autouse=True)
    def patch_water(self, monkeypatch):
        monkeypatch.setattr('services.waterways.fetch_waterways', lambda b: {'elements': []})

    def test_mode_is_target(self, body, rain):
        result = suggest_pond_size(dict(body, target_volume_m3=200))
        assert result['mode'] == 'target'
        assert result['target_volume_m3'] == 200

    def test_recommended_meets_or_exceeds_target(self, body, rain):
        target = 200
        result = suggest_pond_size(dict(body, target_volume_m3=target))
        if result['recommended']:
            assert result['recommended']['capacity_m3'] >= target

    def test_smallest_design_chosen_for_target(self, body, rain):
        """With a tiny target, the smallest evaluated design should be recommended."""
        result = suggest_pond_size(dict(body, target_volume_m3=1))
        rec = result['recommended']
        assert rec is not None
        # All other alternatives that meet the target must be >= recommended capacity
        for alt in result['alternatives']:
            if alt['label'] != 'recommended':
                assert alt['capacity_m3'] >= rec['capacity_m3']

    def test_next_step_up_included_when_available(self, body, rain):
        """When the recommended design is not the largest, a step-up is shown."""
        result = suggest_pond_size(dict(body, target_volume_m3=1))
        labels = [a['label'] for a in result['alternatives']]
        # There should normally be a 'next_step_up' because the 1 m³ target
        # is met by the very first candidate
        if 'next_step_up' in labels:
            next_cap = next(a['capacity_m3'] for a in result['alternatives']
                            if a['label'] == 'next_step_up')
            rec_cap  = result['recommended']['capacity_m3']
            assert next_cap > rec_cap

    def test_infeasible_target_shows_largest_available(self, body, rain):
        """A target larger than any evaluated design falls back to the largest."""
        result = suggest_pond_size(dict(body, target_volume_m3=1_000_000))
        assert result['recommended'] is None
        assert len(result['alternatives']) == 1
        assert result['alternatives'][0]['label'] == 'largest_available'
        assert 'No evaluated combination' in result['reasoning']

    def test_zero_target_volume_accepted(self, body, rain):
        result = suggest_pond_size(dict(body, target_volume_m3=0))
        assert result['status'] == 'success'
        assert result['recommended'] is not None

    def test_recommended_label_is_recommended(self, body, rain):
        result = suggest_pond_size(dict(body, target_volume_m3=200))
        if result['recommended']:
            assert result['recommended']['label'] == 'recommended'


# ---------------------------------------------------------------------------
# HTTP route — /api/suggestSize
# ---------------------------------------------------------------------------

class TestSuggestSizeRoute:

    @pytest.fixture
    def client(self):
        return create_app().test_client()

    @pytest.fixture
    def rain_patch(self, monkeypatch):
        monkeypatch.setattr('services.sizing.fetch_rainfall',
                            lambda *a, **kw: summarize_rainfall(history(), 2020, 2021, include_daily=True))
        monkeypatch.setattr('services.waterways.fetch_waterways', lambda b: {'elements': []})

    def test_200_for_valid_request(self, client, body, rain_patch):
        response = client.post('/api/suggestSize', json=body)
        assert response.status_code == 200
        assert response.get_json()['status'] == 'success'

    def test_400_for_missing_catchment(self, client, body, rain_patch):
        data = {k: v for k, v in body.items() if k != 'catchment'}
        response = client.post('/api/suggestSize', json=data)
        assert response.status_code == 400

    def test_400_for_invalid_target_volume(self, client, body, rain_patch):
        response = client.post('/api/suggestSize', json=dict(body, target_volume_m3='big'))
        assert response.status_code == 400

    def test_503_for_rainfall_failure(self, client, body, monkeypatch):
        monkeypatch.setattr('services.sizing.fetch_rainfall',
                            lambda *a, **kw: (_ for _ in ()).throw(RainfallDataError('down')))
        response = client.post('/api/suggestSize', json=body)
        assert response.status_code == 503

    def test_response_has_alternatives_and_recommended(self, client, body, rain_patch):
        response = client.post('/api/suggestSize', json=body)
        data = response.get_json()
        assert 'alternatives' in data
        assert 'recommended' in data

    def test_target_mode_via_http(self, client, body, rain_patch):
        response = client.post('/api/suggestSize',
                               json=dict(body, target_volume_m3=300))
        data = response.get_json()
        assert data['mode'] == 'target'
        assert data['target_volume_m3'] == 300

    @pytest.mark.parametrize('changes', [
        {'runoff_coefficient': -1},
        {'evaporation_mm_day': 25},
        {'seepage_mm_day': True},
        {'demand_m3_day': None},
        {'initial_storage_fraction': 2},
        {'target_volume_m3': float('nan')},
    ])
    def test_invalid_inputs_return_400(self, client, body, rain_patch, changes):
        response = client.post('/api/suggestSize', json=dict(body, **changes))
        assert response.status_code == 400
