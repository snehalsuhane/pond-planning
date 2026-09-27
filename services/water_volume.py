"""Simple annual runoff estimates; these are inflows, not pond storage capacities."""
import logging
import math

from services.rainfall import fetch_rainfall, RainfallDataError

DEFAULT_RUNOFF_COEFFICIENT = 0.30


def parse_runoff_coefficient(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        coefficient = float(value)
        if not math.isfinite(coefficient) or not 0 <= coefficient <= 1:
            raise ValueError
        return coefficient
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('runoff_coefficient must be a number between 0 and 1.') from exc


def add_water_volumes(result, coefficient, land=None):
    """Enrich successful API results without changing sites, catchments or ranking.

    Use one regional rainfall point for all alternatives: selection centroid,
    or the mean site location when the uploaded survey has no land selection.
    """
    sites = result.get('pond_candidates', [])
    result['water_volume_model'] = {
        'formula': 'mean_annual_rainfall_mm / 1000 * catchment_area_m2 * runoff_coefficient',
        'runoff_coefficient': coefficient, 'unit': 'm3/year',
        'assumptions': 'Uniform regional rainfall and a constant runoff fraction. All modeled runoff reaches the collection target. No storage, evaporation, seepage or conveyance losses are calculated. Overlapping alternatives must not be added together.',
        'coefficient_basis': 'User-adjustable assumption; the default 0.30 is illustrative, not calibrated.'}
    if not sites:
        result['rainfall'] = {'status': 'not_requested', 'reason': 'No pond candidates.'}
        return
    if land is not None:
        longitude, latitude = land.centroid.x, land.centroid.y
    else:
        latitude = sum(s['latitude'] for s in sites) / len(sites)
        longitude = sum(s['longitude'] for s in sites) / len(sites)
    try:
        rainfall = fetch_rainfall(latitude, longitude)
    except RainfallDataError as exc:
        logging.getLogger(__name__).warning('Rainfall lookup: %s', exc)
        rainfall = {'status': 'unavailable', 'reason': 'Historical rainfall could not be retrieved. Retry the analysis to estimate water volume.'}
    result['rainfall'] = rainfall
    for site in sites:
        uncertainty_reasons = []
        if site['catchment'].get('boundary_truncated', False):
            uncertainty_reasons.append({
                'code': 'catchment_reaches_terrain_edge',
                'message': 'Catchment reaches the analyzed terrain edge; upstream land may be missing.'})
        if site.get('assessment', {}).get('routing_sensitivity_fraction', 0) > .5:
            uncertainty_reasons.append({
                'code': 'overflow_sensitive_catchment',
                'message': 'Catchment area and runoff depend strongly on upstream depressions filling and overflowing.'})
        available = rainfall['status'] == 'available'
        site['water_volume'] = {
            'annual_m3': (rainfall['mean_annual_mm'] / 1000 * site['catchment']['area_m2'] * coefficient) if available else None,
            'unit': 'm3/year',
            'uncertainty_reasons': uncertainty_reasons,
            'status': ('provisional' if uncertainty_reasons else 'estimated') if available else 'unavailable',
        }
