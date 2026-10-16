"""Analyze selected land using public terrain and the existing planning algorithms."""
import logging

from pyproj import Transformer
from utils.projection import get_utm_crs_info
from utils.land_selection import land_candidate_mask, LandSelectionError
from analysis.terrain import calculate_slope
from analysis.hydrology import run_hydrology
from analysis.pond import rank_pond_candidates, PondCandidateError
from services.elevation import fetch_elevation, ElevationDataError, ElevationSelectionError
from services.waterways import screen_waterways, WaterwayDataError

# OpenTelemetry
try:
    from telemetry.decorators import span as otel_span
    from telemetry.setup import get_metrics
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    from contextlib import contextmanager
    @contextmanager
    def otel_span(name, attributes=None):  # type: ignore[misc]
        yield None
    def get_metrics(): return {}


def analyze_selected_land(land, *, edge_setback_m=100., water_buffer_m=30.,
                          max_slope_deg=8., buffers=(2000., 4000.),
                          progress_token=None):
    """Try a wider terrain extent once if a returned catchment touches its edge.

    Each extent is ranked and water-screened independently. If expansion fails,
    retain the earlier screened result with its original truncation flags.

    progress_token, when provided, is updated with human-readable stage names
    at each pipeline step so a polling endpoint can surface progress to clients.
    """
    from services.progress import report
    _log = logging.getLogger(__name__)
    app_metrics = get_metrics()
    previous = None
    for buffer_m in buffers:
        try:
            report(progress_token, 'Retrieving elevation\u2026')
            with otel_span('area.fetch_elevation', {'buffer_m': buffer_m}):
                elev_ctr = app_metrics.get('elevation_fetches_total')
                if elev_ctr:
                    elev_ctr.add(1, attributes={'source': 'copernicus_glo30'})
                dem, epsg, source = fetch_elevation(land, buffer_m=buffer_m)
            _log.info('area.fetch_elevation: epsg=%s source=%s', epsg, source)

            report(progress_token, 'Checking terrain\u2026')
            with otel_span('area.land_candidate_mask', {'epsg': str(epsg)}):
                candidate_mask, land_metadata = land_candidate_mask(land, dem, epsg)

            with otel_span('area.calculate_slope'):
                slope = calculate_slope(dem)
            _log.info('area.slope: min=%.2f max=%.2f mean=%.2f deg',
                      slope['slope_min'], slope['slope_max'], slope['slope_mean'])

            report(progress_token, 'Tracing drainage\u2026')
            with otel_span('area.run_hydrology'):
                hydro = run_hydrology(dem)
            _log.info('area.hydrology: acc_max=%s channel_fraction=%.4f',
                      hydro['acc_max'], hydro['channel_fraction'])

            report(progress_token, 'Checking mapped water\u2026')
            with otel_span('area.screen_waterways', {'buffer_m': water_buffer_m}):
                ww_ctr = app_metrics.get('waterway_screens_total')
                if ww_ctr:
                    ww_ctr.add(1, attributes={'source': 'osm'})
                water, water_metadata = screen_waterways(dem, epsg, buffer_m=water_buffer_m)

            report(progress_token, 'Ranking pond sites\u2026')
            with otel_span('area.rank_pond_candidates', {
                'edge_setback_m': edge_setback_m,
                'max_slope_deg': max_slope_deg,
            }):
                ranked = rank_pond_candidates(dem, slope, epsg, hydrology=hydro,
                                             candidate_mask=candidate_mask, exclusion_mask=water,
                                             edge_setback_m=edge_setback_m, max_slope_deg=max_slope_deg)
        except (ElevationDataError, ElevationSelectionError, LandSelectionError,
                WaterwayDataError, PondCandidateError) as exc:
            logging.getLogger(__name__).warning('Selected-land analysis at %sm: %s', buffer_m, exc)
            if previous:
                previous['planning']['expansion_status'] = 'unavailable'
                previous['planning']['coverage_note'] = 'A larger terrain extent could not be analyzed. These results use the earlier screened extent; edge-reaching catchments remain provisional.'
                report(progress_token, 'done')
                return previous, 200
            if isinstance(exc, WaterwayDataError):
                report(progress_token, 'error')
                return {'status': 'error', 'error_code': 'waterway_screening_unavailable',
                        'waterway_screening': {'status': 'unavailable'},
                        'error': 'Could not check for existing rivers and water bodies. Check the server connection and retry. No unscreened sites were selected.'}, 503
            report(progress_token, 'error')
            return {'status': 'error', 'error': str(exc)}, 503 if isinstance(exc, ElevationDataError) else 422

        sites = [{k: v for k, v in s.items() if k not in {'grid_row', 'grid_col'}}
                 for s in ranked['pond_candidates']]

        # OTel: record domain metrics
        n_sites = len(sites)
        _log.info('area.rank_pond_candidates: %d candidate sites found (buffer_m=%s)', n_sites, buffer_m)
        pond_ctr = app_metrics.get('pond_analyses_total')
        if pond_ctr:
            pond_ctr.add(1, attributes={'source': 'api_land_area', 'status': 'success'})
        cand_hist = app_metrics.get('pond_candidates_found')
        if cand_hist:
            cand_hist.record(n_sites, attributes={'source': 'api_land_area'})

        # Report the raster's outer cell edges in geographic coordinates.
        b, half = dem['bounds'], dem['resolution_m']/2
        corners = [(b['min_x']-half, b['min_y']-half), (b['max_x']+half, b['min_y']-half),
                   (b['max_x']+half, b['max_y']+half), (b['min_x']-half, b['max_y']+half)]
        project = Transformer.from_crs(epsg, 4326, always_xy=True)
        ring = [list(project.transform(x, y)) for x, y in corners + corners[:1]]
        truncated = any(s['catchment']['boundary_truncated'] for s in sites)
        previous = {
            'status': 'success', 'terrain_source': source, 'land_selection': land_metadata,
            'terrain': {'crs': get_utm_crs_info(epsg), 'geometry': {'type': 'Polygon', 'coordinates': [ring]},
                        'min_elevation_m': dem['elevation_min'], 'max_elevation_m': dem['elevation_max']},
            'dem': {k: dem[k] for k in ('resolution_m', 'shape', 'bounds', 'elevation_min', 'elevation_max', 'nan_fraction')},
            'pond_candidates': sites, 'waterway_screening': water_metadata,
            'planning': {'site_scope': 'selected_land', 'terrain_scope': 'buffered_public_dem',
                         'catchments_clipped_to_land': False, 'terrain_buffer_m': buffer_m,
                         'expansion_status': 'limit_reached' if truncated else 'not_needed' if buffer_m == buffers[0] else 'expanded',
                         'assessment': 'preliminary terrain screening', 'catchment_display_unit': 'ha',
                         'edge_setback_m': edge_setback_m, 'ranking_version': 'collection_targets_v3',
                         'collection_screening': ranked['screening']},
            'hydrology': {k: hydro[k] for k in ('noflow_count', 'acc_max', 'acc_mean', 'channel_threshold', 'channel_cell_count')},
        }
        if not truncated:
            break
    report(progress_token, 'done')
    return previous, 200
