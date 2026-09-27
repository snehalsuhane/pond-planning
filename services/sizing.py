"""Pond-size recommendation engine, driven by the seasonal water-balance model.

Evaluates a small grid of excavation lengths, widths and depths, runs the
historical water balance for each, and returns:

* With ``target_volume_m3``: the smallest design that meets the target, plus
  the next step up; fills the target from historical runoff if possible.
* Without a target: three labelled tiers (small / intermediate / large) with
  a transparent default choice and an explanation of why it was chosen.

Terrain ranking is deliberately kept separate.  Adding capacity directly to
the existing score would count catchment area a second time.

Public API
----------
suggest_pond_size(data)  ->  dict
"""

import math
from pyproj import Transformer
from shapely.geometry import MultiPolygon, shape
from shapely.ops import transform

from services.pond_design import design_pond, number
from services.rainfall import fetch_rainfall
from services.storage import daily_balance, seasonal_summary
from utils.land_selection import parse_land_area
from utils.projection import get_utm_epsg

# ---------------------------------------------------------------------------
# Candidate footprint grid
# ---------------------------------------------------------------------------
# Lengths and widths to evaluate (rim dimensions, metres).
# The list is intentionally small: three lengths × three widths × two depths
# = 18 cells, each requiring a full 10-year daily simulation.  The same side
# slope / freeboard / margin as the design defaults are used so that capacity
# matches what the user sees in the design panel.
#
# Adjusting these constants changes the alternative set without touching any
# other logic.
_LENGTHS   = [30, 45, 60]   # rim length (m)   – small / intermediate / large
_WIDTHS    = [20, 30, 40]   # rim width (m)
_DEPTHS    = [1.5, 2.5]     # excavation depth (m)
_SIDE_SLOPE = 2.0           # horizontal : vertical
_FREEBOARD  = 0.5           # m above water surface
_MARGIN     = 5.0           # clearance around rim (m)

# Labels for the three tiers when no target is given.
_TIER_LABELS = ['small', 'intermediate', 'large']


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sloped_capacity(length, width, depth, slope=_SIDE_SLOPE, freeboard=_FREEBOARD):
    """Trapezoidal prismatoid capacity below freeboard (m³)."""
    water_depth  = depth - freeboard
    bottom_l     = length - 2 * slope * depth
    bottom_w     = width  - 2 * slope * depth
    if min(bottom_l, bottom_w) <= 0:
        return None            # geometry invalid for these parameters
    mid_l   = bottom_l + slope * water_depth
    mid_w   = bottom_w + slope * water_depth
    water_l = length - 2 * slope * freeboard
    water_w = width  - 2 * slope * freeboard
    return water_depth / 6 * (bottom_l*bottom_w + 4*mid_l*mid_w + water_l*water_w)


def _candidate_grid(lengths=None, widths=None, depths=None):
    """Return all valid (length, width, depth, capacity_m3) tuples."""
    lengths = lengths or _LENGTHS
    widths  = widths  or _WIDTHS
    depths  = depths  or _DEPTHS
    grid = []
    for length in lengths:
        for width in widths:
            for depth in depths:
                cap = _sloped_capacity(length, width, depth)
                if cap is not None:
                    grid.append((length, width, depth, round(cap, 2)))
    # Sort ascending by capacity so 'smallest-first' logic is trivial.
    return sorted(grid, key=lambda t: t[3])


def _evaluate_candidate(
    length, width, depth, capacity,
    data, daily_mm, runoff_area, project,
    coefficient, evaporation, seepage, demand, initial
):
    """Run the seasonal balance for one (length, width, depth) combination.

    Fabricates a minimal ``design`` dict compatible with ``daily_balance`` and
    ``seasonal_summary`` without triggering the full land / water checks.
    """
    water_depth  = depth - _FREEBOARD
    bottom_l     = length - 2 * _SIDE_SLOPE * depth
    bottom_w     = width  - 2 * _SIDE_SLOPE * depth
    footprint_a  = length * width           # rim area, m²

    design_stub = {
        'capacity_m3':        capacity,
        'water_depth_m':      water_depth,
        'bottom_length_m':    bottom_l,
        'bottom_width_m':     bottom_w,
        'footprint_area_m2':  footprint_a,
        'dimensions':         {'side_slope': _SIDE_SLOPE},
    }

    balance  = daily_balance(daily_mm, design_stub, runoff_area,
                             coefficient, evaporation, seepage, demand, initial)
    seasonal = seasonal_summary(balance['monthly'], capacity_m3=capacity)
    s        = seasonal['summary']

    return {
        # ── Geometry ──────────────────────────────────────────────────────────
        'length_m':             length,
        'width_m':              width,
        'depth_m':              depth,
        'water_depth_m':        water_depth,
        'capacity_m3':          capacity,
        'footprint_area_m2':    footprint_a,
        # ── Water-balance headline statistics ─────────────────────────────────
        'fill_rate_pct':              s['fill_rate_pct'],
        'years_pond_filled':          s['years_pond_filled'],
        'mean_annual_overflow_m3':    s['mean_annual_overflow_m3'],
        'mean_end_monsoon_storage_m3': s['mean_end_monsoon_storage_m3'],
        'mean_end_monsoon_storage_pct': s['mean_end_monsoon_storage_pct'],
        'mean_annual_inflow_m3':      (balance['totals']['inflow_m3']
                                       / max(1, len(balance['annual']))),
        'runoff_supports_fill': s['fill_rate_pct'] > 0,
        # ── Full detail (optional; callers may strip for compactness) ──────────
        'seasonal': seasonal,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def suggest_pond_size(data):
    """Suggest one or more pond designs at the requested site and catchment.

    Parameters
    ----------
    data : dict
        JSON body.  Required keys mirror ``/api/simulatePond``:

        * ``site``           – ``{latitude, longitude}``
        * ``land_area``      – GeoJSON Polygon bounding the allowed excavation
        * ``catchment``      – GeoJSON Polygon/MultiPolygon from terrain result

        Optional hydrological inputs (same defaults as ``/api/simulatePond``):

        * ``runoff_coefficient``     (0–1, default 0.3)
        * ``evaporation_mm_day``     (0–20, default 4)
        * ``seepage_mm_day``         (0–20, default 1)
        * ``demand_m3_day``          (0–100000, default 0)
        * ``initial_storage_fraction`` (0–1, default 0)

        Size recommendation inputs:

        * ``target_volume_m3``  float ≥ 0, optional.
          When given, return the smallest evaluated design that reaches this
          capacity, plus the next step up if one exists.
          When omitted, return small / intermediate / large alternatives.

    Returns
    -------
    dict with keys:
        ``mode``          – ``'target'`` or ``'alternatives'``
        ``alternatives``  – list of evaluated candidate dicts, labelled and ranked
        ``recommended``   – the candidate chosen as the default (dict or None)
        ``reasoning``     – plain-English explanation of the recommendation
        ``assumptions``   – hydrological and grid assumptions applied
        ``rainfall``      – NASA POWER provenance (without daily data)
        ``runoff_area_m2``– catchment area minus pond footprint
    """
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object with site, land_area, catchment and optional sizing inputs.')

    # ── Validate and project catchment ──────────────────────────────────────
    geo = data.get('catchment')
    try:
        if not isinstance(geo, dict):
            raise ValueError()
        if geo.get('type') == 'MultiPolygon':
            parts = geo['coordinates']
            if not parts or sum(len(ring) for part in parts for ring in part) > 100000:
                raise ValueError()
            catchment = MultiPolygon([
                parse_land_area({'type': 'Polygon', 'coordinates': part})
                for part in parts
            ])
        else:
            catchment = parse_land_area(geo)
        site = data.get('site', {})
        lon = float(site.get('longitude'))
        lat = float(site.get('latitude'))
        if (not catchment.is_valid or catchment.bounds[1] < -80 or catchment.bounds[3] > 84
                or max(abs(catchment.bounds[0] - lon), abs(catchment.bounds[2] - lon),
                       abs(catchment.bounds[1] - lat), abs(catchment.bounds[3] - lat)) > 3):
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('catchment must be a valid local GeoJSON Polygon or MultiPolygon.') from exc

    # ── Hydrological parameters ──────────────────────────────────────────────
    coefficient  = number(data, 'runoff_coefficient',       .3,  0,      1)
    evaporation  = number(data, 'evaporation_mm_day',        4,  0,     20)
    seepage      = number(data, 'seepage_mm_day',            1,  0,     20)
    demand       = number(data, 'demand_m3_day',             0,  0, 100000)
    initial      = number(data, 'initial_storage_fraction',  0,  0,      1)

    # ── Target volume (optional) ─────────────────────────────────────────────
    target_raw = data.get('target_volume_m3')
    target_m3  = None
    if target_raw is not None:
        try:
            if isinstance(target_raw, bool):
                raise ValueError
            target_m3 = float(target_raw)
            if not math.isfinite(target_m3) or target_m3 < 0:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError('target_volume_m3 must be a non-negative number.') from exc

    # ── Project catchment and fetch rainfall ─────────────────────────────────
    project = Transformer.from_crs(4326, get_utm_epsg(lon, lat), always_xy=True)
    catchment_utm = transform(project.transform, catchment)
    land = parse_land_area(data['land_area'])
    rainfall = fetch_rainfall(land.centroid.y, land.centroid.x, include_daily=True)
    daily_mm = rainfall['daily_mm']

    # ── Evaluate candidate grid ───────────────────────────────────────────────
    # For each candidate: subtract its own footprint from catchment area so we
    # do not double-count rain that falls on the excavation rim.  We use a fixed
    # orientation-0 (north-aligned) rectangle centred on the site for the
    # overlap calculation; the area difference is small and direction-invariant.
    from shapely.geometry import box as shapely_box
    from shapely.affinity import translate

    utm_x, utm_y = project.transform(lon, lat)

    grid      = _candidate_grid()
    evaluated = []
    for (length, width, depth, capacity) in grid:
        rim_box      = translate(
            shapely_box(-width / 2, -length / 2, width / 2, length / 2),
            utm_x, utm_y
        )
        runoff_area  = catchment_utm.difference(rim_box).area
        result       = _evaluate_candidate(
            length, width, depth, capacity,
            data, daily_mm, runoff_area, project,
            coefficient, evaporation, seepage, demand, initial,
        )
        result['runoff_area_m2'] = runoff_area
        evaluated.append(result)

    # ── Recommend ────────────────────────────────────────────────────────────
    mode         = 'target' if target_m3 is not None else 'alternatives'
    alternatives = []
    recommended  = None
    reasoning    = ''

    if mode == 'target':
        # Smallest design that meets or exceeds the requested capacity.
        meeting = [c for c in evaluated if c['capacity_m3'] >= target_m3]
        if not meeting:
            largest = evaluated[-1]
            alternatives = [dict(largest, label='largest_available')]
            reasoning = (
                f'No evaluated combination reaches the requested {target_m3:.0f} m³. '
                f'The largest available design ({largest["capacity_m3"]:.0f} m³, '
                f'{largest["length_m"]}×{largest["width_m"]} m, {largest["depth_m"]} m deep) '
                'is shown. Consider a larger footprint or greater depth if the site permits.'
            )
        else:
            chosen = meeting[0]          # smallest that meets the target
            alternatives = [dict(chosen, label='recommended')]
            recommended  = dict(chosen, label='recommended')
            # Also show the next step up if available.
            idx = evaluated.index(chosen)
            if idx + 1 < len(evaluated):
                nxt = evaluated[idx + 1]
                alternatives.append(dict(nxt, label='next_step_up'))
            fill_note = (
                'Historical runoff fills it in at least one year in ten.'
                if chosen['runoff_supports_fill']
                else 'Historical runoff has not filled it in any of the ten modelled years. '
                     'Consider a smaller design or supplementary supply.'
            )
            reasoning = (
                f'The smallest design that meets {target_m3:.0f} m³ is '
                f'{chosen["length_m"]}×{chosen["width_m"]} m, {chosen["depth_m"]} m deep '
                f'({chosen["capacity_m3"]:.0f} m³). {fill_note} '
                f'Fill rate: {chosen["fill_rate_pct"]:.0f}% of modelled years.'
            )

    else:
        # Three tiers: pick one from each tercile by capacity.
        n = len(evaluated)
        # Indices that bracket three roughly equal thirds.
        tier_indices = [
            max(0, round(n * 0 / 3)),          # small  – first candidate
            max(0, round(n * 1 / 3)),           # intermediate
            min(n - 1, round(n * 2 / 3)),       # large
        ]
        # Deduplicate while preserving order.
        seen, tiers = set(), []
        for idx in tier_indices:
            if idx not in seen:
                seen.add(idx)
                tiers.append(evaluated[idx])

        for tier, label in zip(tiers, _TIER_LABELS[:len(tiers)]):
            alternatives.append(dict(tier, label=label))

        # Default choice: prefer the intermediate tier unless it never fills,
        # in which case fall back to small.  Never silently pick the largest.
        default_label = 'intermediate' if len(tiers) >= 2 else 'small'
        intermediate  = next((a for a in alternatives if a['label'] == 'intermediate'), None)
        if intermediate is not None and not intermediate['runoff_supports_fill']:
            default_label = 'small'
        recommended = next((a for a in alternatives if a['label'] == default_label), alternatives[0])

        fill_desc = (
            f'{recommended["fill_rate_pct"]:.0f}% of modelled years'
            if recommended['fill_rate_pct'] > 0
            else 'no modelled years'
        )
        reasoning = (
            f'Three alternatives are shown (small / intermediate / large). '
            f'The {recommended["label"]} option ({recommended["length_m"]}×{recommended["width_m"]} m, '
            f'{recommended["depth_m"]} m deep, {recommended["capacity_m3"]:.0f} m³) is the default '
            f'because it balances excavation cost and storage; historical runoff filled it in {fill_desc}. '
            'Rainfall alone does not uniquely determine one correct size — local soil, earthwork cost, '
            'and water-use needs all matter. Review all three alternatives before choosing.'
        )

    return {
        'status':         'success',
        'mode':           mode,
        'target_volume_m3': target_m3,
        'alternatives':   alternatives,
        'recommended':    recommended,
        'reasoning':      reasoning,
        'runoff_area_m2': evaluated[0]['runoff_area_m2'] if evaluated else None,
        'rainfall':       {k: v for k, v in rainfall.items() if k != 'daily_mm'},
        'assumptions': {
            'runoff_coefficient':       coefficient,
            'evaporation_mm_day':       evaporation,
            'seepage_mm_day':           seepage,
            'demand_m3_day':            demand,
            'initial_storage_fraction': initial,
            'side_slope':               _SIDE_SLOPE,
            'freeboard_m':              _FREEBOARD,
            'margin_m':                 _MARGIN,
            'lengths_evaluated_m':      _LENGTHS,
            'widths_evaluated_m':       _WIDTHS,
            'depths_evaluated_m':       _DEPTHS,
            'note': (
                'Sizing evaluates a fixed grid of footprint and depth combinations. '
                'Capacity uses the same trapezoidal prismatoid formula as /api/designPond. '
                'The water balance reuses historical daily rainfall from NASA POWER '
                'and the same seasonal model as /api/simulatePond. '
                'Direct rain on the excavation rim is included; catchment runoff excludes '
                'the footprint area to avoid double-counting. '
                'Side slope, freeboard and margin are fixed at the grid values above. '
                'This ranking is independent of terrain suitability scores. '
                'Soil, earthworks cost and field conditions are not assessed.'
            ),
        },
        'explanation': (
            'Size recommendations are illustrative scenarios based on historical rainfall, '
            'not a guarantee of stored water. Each candidate uses the same seasonal '
            'water-balance model as /api/simulatePond with the hydrological assumptions above. '
            'The recommended option is a starting point; all alternatives should be reviewed. '
            'A design that never fills historically may still be worthwhile if it captures '
            'occasional large storms. Conversely, a pond that fills every year may be '
            'undersized relative to demand.'
        ),
    }
