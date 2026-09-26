"""
Tests: test_pond

Unit tests for analysis/terrain.calculate_slope() and analysis/pond.find_pond_candidate().

All tests use synthetic data — no dependency on sample KML coordinates.
"""

import math
import pytest
import numpy as np

from analysis.terrain import calculate_slope
from analysis.pond import find_pond_candidate, rank_pond_candidates, PondCandidateError
from shapely.geometry import Point, shape
from analysis.dem import generate_dem
from analysis.hydrology import run_hydrology


# ---------------------------------------------------------------------------
# Synthetic data helpers  (shared with test_dem.py style)
# ---------------------------------------------------------------------------

def _dome_contours(n=5, base=270.0, interval=5.0, cx=360000.0, cy=2350000.0, spacing=150.0):
    contours = []
    for level in range(n):
        elev = base + level * interval
        radius = (n - level) * spacing
        n_pts = max(24, int(2 * math.pi * radius / 5))
        proj = [
            [cx + radius * math.cos(2 * math.pi * i / n_pts),
             cy + radius * math.sin(2 * math.pi * i / n_pts)]
            for i in range(n_pts)
        ]
        proj.append(proj[0])
        contours.append({
            "id": level, "elevation": elev,
            "coordinates": [[0.0, 0.0]] * len(proj),
            "projected_coordinates": proj,
        })
    return contours


def _flat_dem_result(rows=30, cols=30, res=5.0, base_elev=270.0):
    """A perfectly flat DEM — useful for isolated slope-logic tests."""
    dem = np.full((rows, cols), base_elev)
    return {
        "dem":          dem,
        "x_coords":     np.arange(cols, dtype=float) * res,
        "y_coords":     np.arange(rows, dtype=float) * res,
        "resolution_m": res,
        "shape":        dem.shape,
        "bounds":       {"min_x": 0.0, "min_y": 0.0,
                         "max_x": cols * res, "max_y": rows * res},
        "nan_fraction":  0.0,
        "elevation_min": float(base_elev),
        "elevation_max": float(base_elev),
    }


def _ramp_dem_result(rows=30, cols=30, res=5.0):
    """A tilted plane — slope increases linearly along the X axis."""
    x = np.tile(np.arange(cols, dtype=float) * res, (rows, 1))
    dem = 270.0 + x * 0.1   # 0.1 m rise per metre run → ~5.7° slope
    return {
        "dem":          dem,
        "x_coords":     np.arange(cols, dtype=float) * res,
        "y_coords":     np.arange(rows, dtype=float) * res,
        "resolution_m": res,
        "shape":        dem.shape,
        "bounds":       {"min_x": 0.0, "min_y": 0.0,
                         "max_x": cols * res, "max_y": rows * res},
        "nan_fraction":  0.0,
        "elevation_min": float(dem.min()),
        "elevation_max": float(dem.max()),
    }


# EPSG for a generic UTM-North zone (India zone 44N)
EPSG = 32644

# Dome-based DEM + slope (generated once for the class)
@pytest.fixture(scope="module")
def dome_dem():
    return generate_dem(_dome_contours(), resolution=10.0)


@pytest.fixture(scope="module")
def dome_slope(dome_dem):
    return calculate_slope(dome_dem)


# ---------------------------------------------------------------------------
# calculate_slope tests
# ---------------------------------------------------------------------------

class TestCalculateSlope:

    def test_returns_dict(self, dome_dem):
        result = calculate_slope(dome_dem)
        assert isinstance(result, dict)

    def test_required_keys(self, dome_dem):
        result = calculate_slope(dome_dem)
        assert {"slope", "slope_min", "slope_max", "slope_mean"}.issubset(result.keys())

    def test_slope_is_ndarray(self, dome_dem):
        result = calculate_slope(dome_dem)
        assert isinstance(result["slope"], np.ndarray)

    def test_slope_shape_matches_dem(self, dome_dem):
        result = calculate_slope(dome_dem)
        assert result["slope"].shape == dome_dem["dem"].shape

    def test_slope_non_negative(self, dome_dem):
        result = calculate_slope(dome_dem)
        assert np.all(result["slope"] >= 0)

    def test_flat_dem_has_zero_slope(self):
        flat = _flat_dem_result()
        result = calculate_slope(flat)
        assert result["slope_max"] < 0.01   # numerically zero

    def test_ramp_slope_is_sensible(self):
        """A 0.1 m/m ramp should give ~5.7° slope."""
        ramp = _ramp_dem_result()
        result = calculate_slope(ramp)
        # Interior cells should be close to arctan(0.1) ≈ 5.71°
        interior = result["slope"][2:-2, 2:-2]
        assert np.allclose(interior, np.degrees(np.arctan(0.1)), atol=0.5)

    def test_slope_min_lte_mean_lte_max(self, dome_dem):
        result = calculate_slope(dome_dem)
        assert result["slope_min"] <= result["slope_mean"] <= result["slope_max"]

    def test_slope_values_are_degrees_not_radians(self, dome_dem):
        """Slope max should be well below π/2 radians (1.57) for any terrain."""
        result = calculate_slope(dome_dem)
        assert result["slope_max"] < 90.0   # degrees, not radians

    def test_missing_dem_key_raises(self):
        with pytest.raises(ValueError, match="dem"):
            calculate_slope({"resolution_m": 5.0})

    def test_missing_resolution_key_raises(self):
        with pytest.raises(ValueError, match="dem"):
            calculate_slope({"dem": np.ones((3, 3))})




def terrain():
    rr,cc=np.indices((31,31))
    z=100.+cc+abs(rr-15)
    z[15,15]-=10
    return {'dem':z,'resolution_m':30.,'x_coords':500000+np.arange(31)*30.,
            'y_coords':2300000+np.arange(31)*30.}


def test_connected_catchment_recovers_upstream_area_past_pit():
    d=terrain()
    allowed=np.zeros((31,31),dtype=bool)
    allowed[15,3]=True
    h=run_hydrology(d)
    raw=run_hydrology(d,fill_pits=False)
    result=rank_pond_candidates(d,calculate_slope(d),32644,hydrology=h,
                               exclusion_mask=~allowed,num_candidates=1)
    site=result['pond_candidates'][0]
    assert site['catchment']['area_m2']==h['flow_accumulation'][15,3]*900
    assert h['flow_accumulation'][15,3]>raw['flow_accumulation'][15,3]
    assert shape(site['catchment']['geometry']).covers(Point(site['longitude'],site['latitude']))
    for removed in ['footprint','spill_point','centre','stage_storage','max_depth_m','potential_storage_m3']:
        assert removed not in site


def test_absolute_elevation_does_not_change_location_ranking():
    d=terrain()
    first=rank_pond_candidates(d,calculate_slope(d),32644)
    d['dem']=d['dem']+500
    second=rank_pond_candidates(d,calculate_slope(d),32644)
    assert [(s['grid_row'],s['grid_col'],s['score']) for s in first['pond_candidates']]==[
        (s['grid_row'],s['grid_col'],s['score']) for s in second['pond_candidates']]


def test_regions_are_not_required_to_select_a_location():
    d=terrain()
    rr,cc=np.indices((31,31))
    d['dem']=100.+cc+abs(rr-15)
    result=rank_pond_candidates(d,calculate_slope(d),32644,num_candidates=1)
    assert result['pond_candidates']
    assert result['pond_candidates'][0]['location_type']=='preliminary_pond_outlet'


def bowl():
    d = terrain()
    rr, cc = np.indices(d['dem'].shape)
    d['dem'] = 100. + np.maximum(abs(rr-15), abs(cc-15))
    return d


def test_depression_collects_all_inflow_instead_of_a_single_bottom_cell():
    d = bowl()
    h = run_hydrology(d)
    site = rank_pond_candidates(d, calculate_slope(d), 32644, hydrology=h,
                               num_candidates=1, include_masks=True)['pond_candidates'][0]
    assert site['collection_type'] == 'natural_depression'
    assert site['assessment']['conditioning_fill_m'] > 0
    r, c = site['grid_row'], site['grid_col']
    assert site['catchment']['area_m2'] > h['flow_accumulation'][r, c] * 900
    # This closed bowl has no external outlet; its whole floor is one target.
    assert (site['_catchment_mask'][h['conditioned_dem'] > d['dem']]).all()
    assert site['catchment']['area_m2'] == site['_catchment_mask'].sum() * 900
    assert 'pour_point' not in site['catchment']
    assert 'footprint' not in site


def test_representative_location_does_not_change_depression_catchment():
    d = bowl()
    slopes = calculate_slope(d)
    first = rank_pond_candidates(d, slopes, 32644, num_candidates=1, include_masks=True)['pond_candidates'][0]
    # Make only the old representative unsuitable; the natural collection area
    # and its drainage still exist and must not shrink to a different pixel trace.
    slopes['slope'][first['grid_row'], first['grid_col']] = 90.
    second = rank_pond_candidates(d, slopes, 32644, num_candidates=1, include_masks=True)['pond_candidates'][0]
    assert (first['grid_row'], first['grid_col']) != (second['grid_row'], second['grid_col'])
    np.testing.assert_array_equal(first['_catchment_mask'], second['_catchment_mask'])


def test_depression_intersecting_mapped_water_is_not_a_pond_target():
    import pytest
    from analysis.pond import PondCandidateError
    d = bowl()
    water = np.zeros_like(d['dem'], dtype=bool)
    water[15, 15] = True
    with pytest.raises(PondCandidateError):
        rank_pond_candidates(d, calculate_slope(d), 32644, exclusion_mask=water)


def test_flat_surface_does_not_create_artificial_pond_outlets():
    import pytest
    from analysis.pond import PondCandidateError
    d = terrain()
    d['dem'][:] = 100.
    with pytest.raises(PondCandidateError):
        rank_pond_candidates(d, calculate_slope(d), 32644)


def test_substantially_larger_drainage_is_preferred_and_duplicate_reaches_removed():
    d = terrain()
    rr, cc = np.indices(d['dem'].shape)
    d['dem'] = 100. + cc + abs(rr - 15)
    h = run_hydrology(d)
    result = rank_pond_candidates(d, calculate_slope(d), 32644, include_masks=True)
    sites = result['pond_candidates']
    assert sites[0]['catchment']['area_m2'] >= .5 * h['flow_accumulation'].max() * 900
    assert all(abs(sum(s['criteria'].values()) - s['score']) < 1e-5 for s in sites)
    for i, site in enumerate(sites):
        for other in sites[:i]:
            a, b = site['_catchment_mask'], other['_catchment_mask']
            assert (a & b).sum() / (a | b).sum() < .8


def test_assessment_reports_upstream_water_buffer_and_filling():
    d = terrain()
    h = run_hydrology(d)
    water = np.zeros_like(d['dem'], dtype=bool)
    water[15, 20] = True
    result = rank_pond_candidates(d, calculate_slope(d), 32644, hydrology=h,
                                  exclusion_mask=water, include_masks=True)
    assert any(s['assessment']['upstream_exclusion_buffer_overlap'] for s in result['pond_candidates'])
    for site in result['pond_candidates']:
        mask = site['_catchment_mask']
        assessment = site['assessment']
        assert not water[site['grid_row'], site['grid_col']]
        assert assessment['upstream_exclusion_buffer_fraction'] == float(water[mask].mean())
        fill = h['conditioned_dem'][mask] - d['dem'][mask]
        assert assessment['catchment_filled_fraction'] == float((fill > 1e-6).mean())
        assert site['catchment']['area_m2'] == mask.sum() * d['resolution_m']**2


def test_single_candidate_wrapper_uses_the_current_ranking():
    d = terrain()
    slopes = calculate_slope(d)
    site = find_pond_candidate(d, slopes, EPSG)['pond_site']
    assert site == rank_pond_candidates(d, slopes, EPSG, num_candidates=1)['pond_candidates'][0]
