"""Illustrative daily pond balance driven by complete historical rainfall."""
from collections import defaultdict
from datetime import datetime
import math
from pyproj import Transformer
from shapely.geometry import MultiPolygon, shape
from shapely.ops import transform
from services.pond_design import design_pond, number
from services.rainfall import fetch_rainfall
from utils.land_selection import parse_land_area
from utils.projection import get_utm_epsg

# ---------------------------------------------------------------------------
# Clearly labelled seasonal assumptions
# ---------------------------------------------------------------------------
# Month numbers (1-based) that define each meteorological season.
# These are the default Indian meteorological seasons used when the caller
# does not supply a custom partition.  Every month 1–12 must appear in
# exactly one season.
MONSOON_MONTHS      = [6, 7, 8, 9]       # June–September: primary rain season
POST_MONSOON_MONTHS = [10, 11]           # October–November: retreating monsoon
DRY_MONTHS          = [12, 1, 2, 3, 4, 5]  # December–May: dry / rabi season

DEFAULT_SEASONS = {
    'monsoon':      MONSOON_MONTHS,
    'post_monsoon': POST_MONSOON_MONTHS,
    'dry':          DRY_MONTHS,
}

TOTALS = ('rainfall_mm', 'runoff_m3', 'direct_rain_m3', 'inflow_m3',
          'overflow_m3', 'evaporation_m3', 'seepage_m3', 'supplied_m3', 'unmet_demand_m3')


def surface_area(storage, design):
    """Invert the exact sloped rectangular stage-volume curve by bisection."""
    if storage <= 0:
        return 0.
    length, width = design['bottom_length_m'], design['bottom_width_m']
    slope = design['dimensions']['side_slope']
    low, high = 0., design['water_depth_m']
    for _ in range(40):
        h = (low + high) / 2
        volume = length*width*h + slope*(length+width)*h*h + 4*slope*slope*h**3/3
        if volume < storage:
            low = h
        else:
            high = h
    return (length+2*slope*h)*(width+2*slope*h)


def daily_balance(daily, design, runoff_area, coefficient, evaporation, seepage, demand, initial):
    """Carry storage continuously; inflow/overflow, evaporation, seepage, then use."""
    capacity = design['capacity_m3']
    storage = initial * capacity
    monthly = {}
    for key, rain in sorted(daily.items()):
        day = datetime.strptime(key, '%Y%m%d')
        month = day.strftime('%Y-%m')
        if month not in monthly:
            monthly[month] = dict.fromkeys(TOTALS, 0.)
            monthly[month].update(month=month, start_storage_m3=storage, days_full=0, days_empty=0)
        row = monthly[month]
        runoff = rain/1000 * runoff_area * coefficient
        direct = rain/1000 * design['footprint_area_m2']
        available = storage + runoff + direct
        overflow = max(0., available-capacity)
        storage = min(capacity, available)
        row['days_full'] += int(storage >= capacity-1e-8)
        area = surface_area(storage, design)
        evap = min(storage, evaporation/1000*area)
        storage -= evap
        seep = min(storage, seepage/1000*area)
        storage -= seep
        supplied = min(storage, demand)
        storage -= supplied
        values = (rain, runoff, direct, runoff+direct, overflow, evap, seep, supplied, demand-supplied)
        for field, value in zip(TOTALS, values):
            row[field] += value
        row['end_storage_m3'] = storage
        row['days_empty'] += int(storage <= 1e-8)
    months = list(monthly.values())
    annual = []
    for year in sorted({row['month'][:4] for row in months}):
        rows = [row for row in months if row['month'].startswith(year)]
        record = {field: math.fsum(row[field] for row in rows) for field in TOTALS}
        record.update(year=int(year), start_storage_m3=rows[0]['start_storage_m3'],
                      end_storage_m3=rows[-1]['end_storage_m3'],
                      days_full=sum(row['days_full'] for row in rows),
                      days_empty=sum(row['days_empty'] for row in rows))
        annual.append(record)
    totals = {field: math.fsum(row[field] for row in months) for field in TOTALS}
    totals.update(start_storage_m3=initial*capacity, end_storage_m3=storage)
    totals['balance_residual_m3'] = (initial*capacity + totals['inflow_m3'] - storage
        - sum(totals[key] for key in ('overflow_m3', 'evaporation_m3', 'seepage_m3', 'supplied_m3')))
    return {'monthly': months, 'annual': annual, 'totals': totals}


# ---------------------------------------------------------------------------
# Seasonal aggregation
# ---------------------------------------------------------------------------

def seasonal_summary(monthly_rows, capacity_m3=None, seasons=None):
    """Aggregate monthly balance rows into named seasons and report key statistics.

    Groups months by their meteorological season, accumulates mass-balance
    fields within each (year, season) bucket, then computes multi-year
    summary statistics:

    * **fill rate** – fraction of years the pond reached capacity at least once
    * **mean annual overflow** – average volume that spilled over capacity
    * **end-of-monsoon storage** – average storage remaining when the main
      rain season ends (the season named ``'monsoon'`` in *seasons*, if present)

    Seasons are defined by calendar-month numbers so that December is always
    attributed to the calendar year it falls in.  This gives a simple,
    consistent grouping across any historical record length.

    Parameters
    ----------
    monthly_rows : list[dict]
        Output of ``daily_balance()['monthly']``.  Each dict must have the
        fields in ``TOTALS`` plus ``month`` (``'YYYY-MM'``),
        ``start_storage_m3``, ``end_storage_m3``, ``days_full``,
        ``days_empty``.
    capacity_m3 : float or None
        Pond capacity in cubic metres.  When supplied, end-of-monsoon storage
        is also expressed as a percentage of capacity.
    seasons : dict[str, list[int]] or None
        Mapping of season name → list of 1-based month numbers.
        Must cover every month 1–12 exactly once.
        Defaults to ``DEFAULT_SEASONS`` (Indian meteorological seasons).

    Returns
    -------
    dict:
        ``by_year``     – list of per-year dicts, each with a ``seasons``
                          sub-dict keyed by season name.
        ``summary``     – multi-year headline statistics.
        ``seasons_used``– the season definition that was applied.
    """
    if seasons is None:
        seasons = DEFAULT_SEASONS

    # Validate partition: every month 1–12 exactly once.
    covered = [m for months in seasons.values() for m in months]
    if sorted(covered) != list(range(1, 13)):
        raise ValueError(
            'seasons must cover every month 1–12 exactly once; '
            f'got {sorted(covered)}'
        )

    # Build a fast month-number → season-name lookup.
    month_to_season = {}
    for name, months in seasons.items():
        for m in months:
            month_to_season[m] = name

    # Group monthly rows by (calendar year, season).
    year_season_rows = defaultdict(lambda: defaultdict(list))
    for row in monthly_rows:
        year_str, month_str = row['month'].split('-')
        year_season_rows[int(year_str)][month_to_season[int(month_str)]].append(row)

    SEASON_TOTALS = TOTALS  # same accumulated fields

    by_year = []
    for year in sorted(year_season_rows):
        year_entry = {'year': year, 'seasons': {}}
        for season_name, rows in year_season_rows[year].items():
            rows = sorted(rows, key=lambda r: r['month'])
            agg = {field: math.fsum(r[field] for r in rows)
                   for field in SEASON_TOTALS}
            agg['start_storage_m3'] = rows[0]['start_storage_m3']
            agg['end_storage_m3']   = rows[-1]['end_storage_m3']
            agg['days_full']  = sum(r['days_full']  for r in rows)
            agg['days_empty'] = sum(r['days_empty'] for r in rows)
            # True when the pond reached capacity on at least one day.
            agg['filled'] = agg['days_full'] > 0
            year_entry['seasons'][season_name] = agg
        by_year.append(year_entry)

    # ── Multi-year summary statistics ────────────────────────────────────────
    n_years = len(by_year)
    years_filled = sum(
        1 for entry in by_year
        if any(s['filled'] for s in entry['seasons'].values())
    )

    annual_overflows = [
        math.fsum(s['overflow_m3'] for s in entry['seasons'].values())
        for entry in by_year
    ]
    mean_overflow = (
        math.fsum(annual_overflows) / n_years if n_years else 0.
    )

    # End-of-monsoon storage uses the season named 'monsoon' if it exists.
    monsoon_name = 'monsoon' if 'monsoon' in seasons else None
    monsoon_end_storages = [
        entry['seasons'][monsoon_name]['end_storage_m3']
        for entry in by_year
        if monsoon_name and monsoon_name in entry['seasons']
    ] if monsoon_name else []

    mean_end_monsoon_m3 = (
        math.fsum(monsoon_end_storages) / len(monsoon_end_storages)
        if monsoon_end_storages else None
    )
    mean_end_monsoon_pct = (
        100. * mean_end_monsoon_m3 / capacity_m3
        if (mean_end_monsoon_m3 is not None and capacity_m3 and capacity_m3 > 0)
        else None
    )

    summary = {
        'years_analysed':             n_years,
        'years_pond_filled':          years_filled,
        'fill_rate_pct':              (100. * years_filled / n_years) if n_years else 0.,
        'mean_annual_overflow_m3':    mean_overflow,
        'mean_end_monsoon_storage_m3':  mean_end_monsoon_m3,
        'mean_end_monsoon_storage_pct': mean_end_monsoon_pct,
        'note': (
            'fill_rate_pct is the percentage of years in which the pond '
            'reached capacity on at least one day. '
            'mean_end_monsoon_storage_m3 is the average storage remaining '
            'at the end of the monsoon season (end of September by default). '
            'Seasons follow calendar years; the dry season spans December '
            'of the same year through May of the same year (Dec is in-year).'
        ),
    }

    return {
        'by_year':      by_year,
        'summary':      summary,
        'seasons_used': {name: sorted(months) for name, months in seasons.items()},
    }


def simulate_pond(data):
    design = design_pond(data, screen_water=False)
    if not design['checks']['within_selected_land']:
        raise ValueError('The pond and margin must fit within the selected land.')
    coefficient = number(data, 'runoff_coefficient', .3, 0, 1)
    evaporation = number(data, 'evaporation_mm_day', 4, 0, 20)
    seepage = number(data, 'seepage_mm_day', 1, 0, 20)
    demand = number(data, 'demand_m3_day', 0, 0, 100000)
    initial = number(data, 'initial_storage_fraction', 0, 0, 1)
    geo = data.get('catchment')
    try:
        if not isinstance(geo, dict):
            raise ValueError()
        if geo.get('type') == 'MultiPolygon':
            parts = geo['coordinates']
            if not parts or sum(len(ring) for part in parts for ring in part) > 100000:
                raise ValueError()
            catchment = MultiPolygon([parse_land_area({'type': 'Polygon', 'coordinates': part}) for part in parts])
        else:
            catchment = parse_land_area(geo)
        lon, lat = data['site']['longitude'], data['site']['latitude']
        if (not catchment.is_valid or catchment.bounds[1] < -80 or catchment.bounds[3] > 84
                or max(abs(catchment.bounds[0]-float(lon)), abs(catchment.bounds[2]-float(lon)),
                       abs(catchment.bounds[1]-float(lat)), abs(catchment.bounds[3]-float(lat))) > 3):
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('catchment must be a valid local GeoJSON Polygon or MultiPolygon.') from exc
    project = Transformer.from_crs(4326, get_utm_epsg(float(lon), float(lat)), always_xy=True)
    catchment = transform(project.transform, catchment)
    footprint = transform(project.transform, shape(design['footprint']))
    # Rim rainfall drains to the pond. Remove its overlap from land runoff to
    # avoid counting the same rain twice, even at a catchment outlet boundary.
    runoff_area = catchment.difference(footprint).area
    land = parse_land_area(data['land_area'])
    rainfall = fetch_rainfall(land.centroid.y, land.centroid.x, include_daily=True)
    result = daily_balance(rainfall['daily_mm'], design, runoff_area, coefficient,
                           evaporation, seepage, demand, initial)
    seasonal = seasonal_summary(result['monthly'], capacity_m3=design['capacity_m3'])
    result.update(
        status='success',
        capacity_m3=design['capacity_m3'],
        runoff_area_m2=runoff_area,
        rainfall={key: value for key, value in rainfall.items() if key != 'daily_mm'},
        seasonal=seasonal,
        assumptions={
            # Hydrological assumptions — clearly labelled
            'runoff_coefficient':       coefficient,   # fraction of catchment rain that reaches pond
            'evaporation_mm_day':       evaporation,   # open-water evaporation depth per day (mm)
            'seepage_mm_day':           seepage,       # downward seepage loss depth per day (mm)
            'demand_m3_day':            demand,        # daily water abstraction volume (m³)
            'initial_storage_fraction': initial,       # pond fill fraction at simulation start (0–1)
            # Seasonal grouping assumptions
            'seasons': seasonal['seasons_used'],
        },
        explanation=(
            'Historical scenario, not a forecast or measured storage. '
            'Daily uniform rainfall and a constant runoff fraction are assumed. '
            'Rain on the whole excavation rim drains into the pond; its overlap '
            'is excluded from catchment runoff to avoid double-counting. '
            'Each day: inflow → overflow above capacity → evaporation → seepage → water use. '
            'Loss depths apply to the water surface after inflow, limited to available water. '
            'Storage carries between years; the initial fraction applies only on the first day. '
            'Seasonal totals group calendar months by meteorological season '
            '(default: Indian meteorological seasons). '
            'Loss defaults are illustrative, not local measurements. '
            'Catchment uncertainty carries into these estimates. '
            'This calculation does not recheck mapped water or recommend dimensions.'
        ),
    )
    return result
