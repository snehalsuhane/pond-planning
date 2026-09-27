"""Selected land restricts collection sites, never their upstream drainage."""
import json

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform

from utils.land_selection import parse_land_area, land_candidate_mask, LandSelectionError
from analysis.hydrology import run_hydrology
from analysis.pond import rank_pond_candidates, PondCandidateError
from analysis.terrain import calculate_slope
from tests.test_pond import terrain, bowl


def geographic(polygon):
    return transform(Transformer.from_crs(32644, 4326, always_xy=True).transform, polygon)


@pytest.mark.parametrize('value', [None, 'bad json', '{}', 'null',
    {'type': 'Point', 'coordinates': [81, 21]},
    {'type': 'Polygon', 'coordinates': []},
    {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 1], [1, 0]]]},
    {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]]},
    {'type': 'Polygon', 'coordinates': [[[181, 0], [182, 0], [182, 1], [181, 0]]]},
    {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [float('nan'), 1], [0, 0]]]},
])
def test_invalid_selection_rejected(value):
    with pytest.raises(LandSelectionError):
        parse_land_area(value)


def test_feature_with_hole_excludes_cells_and_reports_coverage():
    d = terrain()
    outer = box(500050, 2300050, 500850, 2300850)
    inner = box(500350, 2300350, 500550, 2300550)
    land = geographic(outer.difference(inner))
    parsed = parse_land_area(json.dumps({'type': 'Feature', 'geometry': mapping(land), 'properties': {}}))
    mask, metadata = land_candidate_mask(parsed, d, 32644)
    assert mask[5, 5] and not mask[15, 15]
    assert metadata['terrain_coverage_fraction'] == pytest.approx(1)
    assert metadata['area_ha'] == pytest.approx(60)
    assert not metadata['partial_terrain_coverage']
    assert metadata['candidate_cell_count'] == mask.sum()


def test_entire_cells_must_fit_inside_boundary():
    d = terrain()
    land = geographic(box(500085, 2300085, 500125, 2300125))
    # Centers at 90/120 are inside, but neither entire 30m cell fits.
    with pytest.raises(LandSelectionError, match='no complete terrain cells'):
        land_candidate_mask(land, d, 32644)


def test_partial_and_missing_terrain_are_distinguished():
    d = terrain()
    land = geographic(box(499900, 2299900, 500400, 2300400))
    _, metadata = land_candidate_mask(land, d, 32644)
    assert metadata['partial_terrain_coverage']
    assert metadata['terrain_coverage_fraction'] == pytest.approx(415**2 / 500**2)
    with pytest.raises(LandSelectionError, match='does not overlap'):
        land_candidate_mask(geographic(box(510000, 2300000, 510100, 2300100)), d, 32644)


def test_invalid_dem_cells_are_not_available_land():
    d = terrain()
    d['valid_mask'] = np.ones_like(d['dem'], dtype=bool)
    d['valid_mask'][10:20, 10:20] = False
    mask, metadata = land_candidate_mask(geographic(box(500100, 2300100, 500800, 2300800)), d, 32644)
    assert not mask[15, 15]
    assert metadata['partial_terrain_coverage']


def test_one_cell_land_keeps_full_upstream_catchment_and_water_semantics():
    d = terrain()
    land = np.zeros_like(d['dem'], dtype=bool)
    land[15, 3] = True
    hydro = run_hydrology(d)
    site = rank_pond_candidates(d, calculate_slope(d), 32644, hydrology=hydro,
                               candidate_mask=land, edge_setback_m=100., include_masks=True)['pond_candidates'][0]
    assert (site['grid_row'], site['grid_col']) == (15, 3)
    assert site['catchment']['area_m2'] == hydro['flow_accumulation'][15, 3] * 900
    assert (site['_catchment_mask'] & ~land).any()
    assert not site['assessment']['upstream_exclusion_buffer_overlap']
    assert site['assessment']['upstream_exclusion_buffer_fraction'] == 0
    assert site['catchment']['boundary_truncated']  # Full domain, not land edge.


def test_depression_crossing_land_is_rejected_not_clipped():
    d = bowl()
    land = np.ones_like(d['dem'], dtype=bool)
    land[15, 16] = False
    with pytest.raises(PondCandidateError, match='selected land'):
        rank_pond_candidates(d, calculate_slope(d), 32644, candidate_mask=land)


def test_unrestricted_mask_preserves_existing_ranking():
    d = terrain()
    slopes = calculate_slope(d)
    assert rank_pond_candidates(d, slopes, 32644) == rank_pond_candidates(
        d, slopes, 32644, candidate_mask=np.ones_like(d['dem'], dtype=bool))


def test_empty_and_wrong_shape_masks():
    d = terrain()
    with pytest.raises(PondCandidateError, match='selected land'):
        rank_pond_candidates(d, calculate_slope(d), 32644, candidate_mask=np.zeros_like(d['dem']))
    with pytest.raises(ValueError, match='match the DEM'):
        rank_pond_candidates(d, calculate_slope(d), 32644, candidate_mask=np.ones((2, 2)))
