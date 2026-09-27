"""
Analysis: pond

Identifies suitable pond candidate locations from a DEM and slope grid.

Selection algorithm
-------------------
Each collection target is scored by combining two normalised criteria:

    score = 0.75 * drainage_penalty + 0.25 * slope_penalty

where:
    drainage_penalty = 1 - log(1 + cells) / log(1 + largest catchment cells)
    slope_penalty    = local mean slope / max_slope_deg, capped at 1

Lower score → better pond site.

Cells that exceed max_slope_deg, lie within the edge border, contain NaNs, or overlap mapped water are excluded.
Spatially distinct candidates are greedily selected based on minimum score and minimum distance.

Public API
----------
rank_pond_candidates(dem_result, slope_result, epsg, ...)
    → dict with a list of ranked pond_candidates

find_pond_candidate(...)
    → optional compatibility wrapper returning the single top candidate
"""

import numpy as np
from scipy.ndimage import label, find_objects, distance_transform_edt, uniform_filter, binary_erosion
from pyproj import Transformer
from analysis.hydrology import run_hydrology, _D8
from analysis.catchment import trace_upstream_catchment
from analysis.raster_geometry import mask_geometry


class PondCandidateError(ValueError):
    """Raised when no suitable pond candidate can be identified."""


def rank_pond_candidates(dem_result, slope_result, epsg, *, hydrology=None,
                         exclusion_mask=None, candidate_mask=None, edge_setback_m=0., max_slope_deg=8.,
                         num_candidates=5, min_distance_m=100., border_cells=3,
                         min_catchment_ha=0., max_catchment_ha=None, include_masks=False):
    """Compare whole natural depressions and outlets on a connected drainage network.

    Depression masks are collection targets, not proposed shorelines. Connected
    catchments assume upstream spilling; the unfilled scenario is reported only
    as a sensitivity check. Neither scenario estimates runoff volume.
    candidate_mask restricts collection targets only; upstream flow still uses
    the full terrain. A depression must be entirely within this mask.
    """
    for name, value in [('edge_setback_m', edge_setback_m), ('min_distance_m', min_distance_m),
                        ('min_catchment_ha', min_catchment_ha)]:
        if not np.isfinite(value) or value < 0:
            raise ValueError(f'{name} must be finite and non-negative')
    if not isinstance(num_candidates, int) or num_candidates < 1:
        raise ValueError('num_candidates must be a positive integer')
    if not np.isfinite(max_slope_deg) or max_slope_deg <= 0:
        raise ValueError('max_slope_deg must be finite and positive')
    if max_catchment_ha is not None and (not np.isfinite(max_catchment_ha) or max_catchment_ha < min_catchment_ha):
        raise ValueError('Invalid catchment range')
    raw = np.asarray(dem_result['dem'], dtype=float)
    slope = np.asarray(slope_result['slope'])
    valid = np.asarray(dem_result.get('valid_mask', np.isfinite(raw)), dtype=bool) & np.isfinite(raw)
    water = np.zeros_like(valid) if exclusion_mask is None else np.asarray(exclusion_mask, dtype=bool)
    land = np.ones_like(valid) if candidate_mask is None else np.asarray(candidate_mask, dtype=bool)
    if slope.shape != raw.shape or water.shape != raw.shape or land.shape != raw.shape:
        raise ValueError('Slope, exclusion and candidate masks must match the DEM')
    res = float(dem_result['resolution_m'])
    if not np.isfinite(res) or res <= 0 or not valid.any():
        raise PondCandidateError('No valid terrain for collection-site screening')
    hydro = hydrology if hydrology is not None else run_hydrology(dem_result)
    acc = hydro['flow_accumulation']
    filled = np.maximum(hydro['conditioned_dem'] - raw, 0)
    distance = distance_transform_edt(np.pad(valid, 1), sampling=res)[1:-1, 1:-1] - res/2
    window = max(3, int(round(30/res)) | 1)
    local_slope = uniform_filter(np.where(valid, slope, 0.), size=window) / np.maximum(
        uniform_filter(valid.astype(float), size=window), 1e-12)
    allowed = valid & land & ~water & (distance >= edge_setback_m) & np.isfinite(slope) & (slope <= max_slope_deg)
    if border_cells:
        allowed[:border_cells] = allowed[-border_cells:] = False
        allowed[:, :border_cells] = allowed[:, -border_cells:] = False
    components, _ = label(valid & (filled > 1e-6), structure=np.ones((3, 3)))
    targets = []

    def add_target(r, c, indices, area_cells, kind):
        area_ha = float(area_cells * res**2 / 10000)
        if area_ha < min_catchment_ha or (max_catchment_ha is not None and area_ha > max_catchment_ha):
            return
        targets.append({'row': int(r), 'col': int(c), 'seeds': indices,
                        'area_cells': int(area_cells), 'kind': kind})

    # A filled depression can have several artificial internal routing branches.
    # Absorb flow at ANY cell of the whole depression, rather than one chosen cell.
    for component, slices in enumerate(find_objects(components), 1):
        local = components[slices] == component
        if local.sum() < 3:  # Suppress isolated raster pits, not physical pond sizes.
            continue
        seeds = np.zeros_like(valid)
        seeds[slices] = local
        if (seeds & water).any() or (seeds & ~land).any() or not (seeds & allowed).any():
            continue
        levels = hydro['conditioned_dem'][seeds]
        if np.ptp(levels) > 1e-5:
            continue  # Adjacent depressions at different spill levels are not one target.
        choices = seeds & allowed
        bottom = float(raw[choices].min())
        choices &= raw <= bottom + 1e-6
        clearance = distance_transform_edt(seeds)
        indices = np.flatnonzero(choices)
        chosen = indices[np.argmax(clearance.ravel()[indices])]
        r, c = np.unravel_index(chosen, raw.shape)
        catchment = trace_upstream_catchment(hydro, seeds)
        add_target(r, c, np.flatnonzero(seeds), catchment.sum(), 'natural_depression')

    # Use the connected network's junctions and last suitable cells along a reach.
    # This chooses collection outlets before scoring, not arbitrary flat pixels.
    threshold = max(3., float(np.percentile(acc[valid], 99)))
    network = valid & (acc >= threshold)
    rr, cc = np.indices(raw.shape)
    nr, nc = rr.copy(), cc.copy()
    for code, (dr, dc, _) in _D8.items():
        m = hydro['flow_direction'] == code
        nr[m] += dr
        nc[m] += dc
    inside = (nr >= 0) & (nr < raw.shape[0]) & (nc >= 0) & (nc < raw.shape[1])
    nr = np.clip(nr, 0, raw.shape[0]-1)
    nc = np.clip(nc, 0, raw.shape[1]-1)
    moving = inside & ((nr != rr) | (nc != cc))
    downhill = raw - raw[nr, nc] > 1e-6
    supported = network & allowed & (components == 0) & moving & downhill
    incoming = np.zeros_like(acc, dtype=int)
    sources = network & moving
    np.add.at(incoming, (nr[sources], nc[sources]), 1)
    nodes = supported & ((incoming >= 2) | ~supported[nr, nc])
    for r, c in np.argwhere(nodes):
        add_target(r, c, np.array([np.ravel_multi_index((r, c), raw.shape)]),
                   acc[r, c], 'drainage_outlet')
    if not targets:
        if candidate_mask is not None:
            raise PondCandidateError('No suitable collection area or drainage outlet lies within the selected land. Try a larger or different boundary; natural depressions must fit entirely inside it.')
        raise PondCandidateError('No suitable natural collection area or drainage outlet passes screening')

    maximum = max(t['area_cells'] for t in targets)
    for target in targets:
        r, c = target['row'], target['col']
        target['criteria'] = {
            'drainage_score': .75 * (1 - np.log1p(target['area_cells']) / np.log1p(maximum)),
            'slope_score': .25 * min(float(local_slope[r, c]) / max_slope_deg, 1),
        }
        target['score'] = sum(target['criteria'].values())
    targets.sort(key=lambda t: (t['score'], -t['area_cells'], t['row'], t['col']))
    direct = run_hydrology(dem_result, fill_pits=False)
    boundary = valid & ~binary_erosion(valid, structure=np.ones((3, 3)), border_value=0)
    project = Transformer.from_crs(epsg, 4326, always_xy=True)
    selected, selected_masks = [], []
    for target in targets:
        r, c = target['row'], target['col']
        if any(np.hypot(r-s['grid_row'], c-s['grid_col'])*res < min_distance_m for s in selected):
            continue
        seeds = np.zeros_like(valid)
        seeds.ravel()[target['seeds']] = True
        catchment = trace_upstream_catchment(hydro, seeds)
        # Nearby points on one reach are alternatives, not five independent basins.
        if any(np.count_nonzero(catchment & other) / np.count_nonzero(catchment | other) >= .8
               for other in selected_masks):
            continue
        unfilled = trace_upstream_catchment(direct, seeds)
        area = float(catchment.sum() * res**2)
        lon, lat = project.transform(dem_result['x_coords'][c], dem_result['y_coords'][r])
        point = {'latitude': float(lat), 'longitude': float(lon)}
        description = {'area_m2': area, 'area_ha': area/10000, 'area_km2': area/1e6,
                       'boundary_truncated': bool((catchment & boundary).any()),
                       'model': 'connected_upstream_drainage_to_collection_target',
                       'interception': 'natural_depression' if target['kind'] == 'natural_depression' else 'outlet_cell',
                       'collection_point': point, **mask_geometry(catchment, dem_result, epsg)}
        if target['kind'] == 'drainage_outlet':
            description['pour_point'] = point
        candidate = {**point, 'grid_row': r, 'grid_col': c, 'rank': len(selected)+1,
                     'location_type': 'preliminary_pond_collection_location' if target['kind'] == 'natural_depression' else 'preliminary_pond_outlet',
                     'collection_type': target['kind'], 'elevation_m': float(raw[r,c]),
                     'slope_deg': float(slope[r,c]), 'local_slope_deg': float(local_slope[r,c]),
                     'distance_to_domain_edge_m': float(distance[r,c]),
                     'score': round(target['score'], 6),
                     'criteria': {k: round(v, 6) for k, v in target['criteria'].items()},
                     'contributing_area_m2': area, 'contributing_area_ha': area/10000,
                     'unfilled_contributing_area_ha': float(unfilled.sum()*res**2/10000),
                     'catchment': description,
                     'assessment': {
                         'status': 'terrain_screening_requires_validation',
                         'conditioning_fill_m': float(filled[r,c]),
                         'catchment_filled_fraction': float(np.mean(filled[catchment] > 1e-6)),
                         'catchment_max_fill_m': float(filled[catchment].max()),
                         'additional_area_in_conditioned_scenario_fraction': float(np.count_nonzero(catchment & ~unfilled)/catchment.sum()),
                         'routing_sensitivity_fraction': float(np.count_nonzero(catchment ^ unfilled)/np.count_nonzero(catchment | unfilled)),
                         'upstream_exclusion_buffer_overlap': bool((catchment & water).any()),
                         'upstream_exclusion_buffer_fraction': float(water[catchment].mean()),
                         'catchment_boundary_truncated': description['boundary_truncated'],
                         'upstream_spill_assumed': True,
                     }}
        if include_masks:
            candidate['_catchment_mask'] = catchment
        selected.append(candidate)
        selected_masks.append(catchment)
        if len(selected) >= num_candidates:
            break
    return {'pond_candidates': selected, 'drainage_saturation_ha': maximum*res**2/10000,
            'screening': {'model': 'collection_targets_v3', 'targets_evaluated': len(targets),
                          'channel_threshold_ha': threshold*res**2/10000,
                          'catchment_overlap_limit': .8}}


def find_pond_candidate(dem_result, slope_result, epsg, **options):
    """Return the first ranked site for callers that need one recommendation."""
    options['num_candidates'] = 1
    return {'pond_site': rank_pond_candidates(dem_result, slope_result, epsg, **options)['pond_candidates'][0]}
