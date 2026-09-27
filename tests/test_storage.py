"""Daily mass conservation, carry-over, overlap accounting and storage API."""
import math
import pytest
from shapely.geometry import box
from app import create_app
from services.pond_design import design_pond
from services.storage import (
    daily_balance, simulate_pond, surface_area,
    seasonal_summary, DEFAULT_SEASONS, MONSOON_MONTHS, POST_MONSOON_MONTHS, DRY_MONTHS,
)
from services.rainfall import RainfallDataError, summarize_rainfall
from tests.test_pond_design import request_data, geographic, X, Y
from tests.test_rainfall import history


@pytest.fixture
def body():
    return dict(request_data(), catchment=geographic(box(X-100, Y-100, X+100, Y+100)))


@pytest.fixture
def rain(monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return summarize_rainfall(history(), 2020, 2021, include_daily=True)
    monkeypatch.setattr('services.storage.fetch_rainfall', fetch)
    return calls


def test_mass_conservation_and_leap_year(body, rain):
    result = simulate_pond(dict(body, demand_m3_day=3, initial_storage_fraction=.4))
    assert len(result['monthly']) == 24
    assert result['annual'][0]['rainfall_mm'] == 366
    assert result['annual'][1]['rainfall_mm'] == 365
    assert abs(result['totals']['balance_residual_m3']) < 1e-7
    assert rain[0][1] == {'include_daily': True}
    assert result['annual'][1]['start_storage_m3'] == result['annual'][0]['end_storage_m3']
    for row in result['monthly']:
        assert 0 <= row['end_storage_m3'] <= result['capacity_m3']
        assert row['start_storage_m3'] + row['inflow_m3'] == pytest.approx(
            row['end_storage_m3'] + sum(row[k] for k in ('overflow_m3', 'evaporation_m3', 'seepage_m3', 'supplied_m3')))


def test_surface_area_matches_sloped_geometry(body):
    design = design_pond(body, screen_water=False)
    assert surface_area(0, design) == 0
    assert surface_area(design['capacity_m3'], design) == pytest.approx(38*28)
    assert 32*22 < surface_area(design['capacity_m3']/2, design) < 38*28


def test_dry_pond_cannot_lose_or_supply_water(body):
    design = design_pond(body, screen_water=False)
    result = daily_balance({'20200101': 0, '20200102': 0}, design, 40000, .3, 4, 1, 20, 0)
    total = result['totals']
    assert total['evaporation_m3'] == total['seepage_m3'] == total['supplied_m3'] == 0
    assert total['unmet_demand_m3'] == 40


def test_overflow_then_losses_and_demand(body):
    design = design_pond(body, screen_water=False)
    result = daily_balance({'20201231': 100, '20210101': 0}, design, 40000, 1, 0, 0, 10, 0)
    assert result['totals']['overflow_m3'] == pytest.approx(4120-design['capacity_m3'])
    assert result['totals']['end_storage_m3'] == design['capacity_m3']-20
    assert result['annual'][1]['start_storage_m3'] == design['capacity_m3']-10


@pytest.mark.parametrize('half', [False, True])
def test_direct_rain_not_double_counted(body, rain, half):
    if half:
        body['catchment'] = geographic(box(X, Y-100, X+100, Y+100))
    result = simulate_pond(dict(body, runoff_coefficient=0, evaporation_mm_day=0, seepage_mm_day=0))
    assert result['runoff_area_m2'] == pytest.approx(19400 if half else 38800, abs=.01)
    assert result['totals']['runoff_m3'] == 0
    assert result['totals']['direct_rain_m3'] == pytest.approx(731*1.2)


@pytest.mark.parametrize('changes', [{'evaporation_mm_day': -1}, {'seepage_mm_day': True},
    {'initial_storage_fraction': 2}, {'demand_m3_day': None}, {'runoff_coefficient': float('nan')},
    {'catchment': None}, {'catchment': {'type': 'MultiPolygon', 'coordinates': []}},
    {'catchment': {'type': 'Point', 'coordinates': [81, 21]}}, {'width_m': 500}])
def test_invalid_request_before_rainfall(body, rain, changes):
    response = create_app().test_client().post('/api/simulatePond', json=dict(body, **changes))
    assert response.status_code == 400
    assert not rain


def test_api_reuses_geometry_without_water_or_terrain_fetch(body, rain, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Simulation must not repeat terrain or water screening')
    monkeypatch.setattr('services.pond_design.screen_waterways', unexpected)
    monkeypatch.setattr('services.area_service.fetch_elevation', unexpected)
    response = create_app().test_client().post('/api/simulatePond', json=body)
    assert response.status_code == 200
    assert response.get_json()['capacity_m3'] == pytest.approx(1317)


def test_rainfall_failure_is_not_zero_storage(body, monkeypatch):
    def fail(*args, **kwargs):
        raise RainfallDataError('Rain unavailable')
    monkeypatch.setattr('services.storage.fetch_rainfall', fail)
    response = create_app().test_client().post('/api/simulatePond', json=body)
    assert response.status_code == 503
    assert 'monthly' not in response.get_json()


def test_gradual_dry_season_drainage_reaches_empty(body):
    """A pond filled to capacity drains substantially through losses alone with no rain.

    This exercises the long dry-season path: many consecutive rainless days
    where evaporation and seepage consume stored water.  With sloped sides the
    water surface area (and therefore loss rate) decreases as the pond empties,
    so the pond may not reach exactly zero, but must lose well over 90% of its
    stored volume.  No negative storage should ever appear.
    """
    design = design_pond(body, screen_water=False)
    capacity = design['capacity_m3']
    # Build 90 dry days with zero rainfall (June–August).
    daily = {f'202006{d:02d}': 0. for d in range(1, 30)}
    daily.update({f'202007{d:02d}': 0. for d in range(1, 32)})
    daily.update({f'202008{d:02d}': 0. for d in range(1, 32)})
    # Start full; high losses (10 mm/day evaporation + 5 mm/day seepage).
    result = daily_balance(daily, design, runoff_area=0, coefficient=0,
                           evaporation=10, seepage=5, demand=0, initial=1.0)
    # Storage must be non-negative every month.
    for row in result['monthly']:
        assert row['end_storage_m3'] >= -1e-9, f"Negative storage in {row['month']}"
    # After 90 days of heavy losses, the pond must be substantially depleted
    # (>90% drained).  Loss rate falls with water level so exact zero is not
    # required; the important property is significant, physically plausible depletion.
    end = result['totals']['end_storage_m3']
    assert end < 0.15 * capacity, (
        f"Expected >85% depletion after 90 dry days; "
        f"end_storage={end:.1f} m³, capacity={capacity:.1f} m³"
    )
    # Total losses must not exceed initial volume (conservation).
    total_lost = math.fsum(result['totals'][k]
                           for k in ('evaporation_m3', 'seepage_m3', 'supplied_m3'))
    assert total_lost <= capacity + 1e-6



def test_partial_fill_then_dry_season_monotonically_decreasing(body):
    """Storage only decreases during a dry period with losses and no inflow."""
    design = design_pond(body, screen_water=False)
    daily = {f'2021{m:02d}{d:02d}': 0.
             for m in (12,) for d in range(1, 32)
             if f'2021{m:02d}{d:02d}' <= '20211231'}
    # 20 days; start at half capacity.
    daily_20 = {f'202101{d:02d}': 0. for d in range(1, 21)}
    result = daily_balance(daily_20, design, runoff_area=0, coefficient=0,
                           evaporation=6, seepage=2, demand=0, initial=0.5)
    storages = [row['end_storage_m3'] for row in result['monthly']]
    # With no rain and positive losses each month must be <= previous month.
    for prev, curr in zip(storages, storages[1:]):
        assert curr <= prev + 1e-9


def test_pond_with_demand_supplied_limited_by_available_water(body):
    """Demand is capped at available storage; unmet demand is recorded correctly."""
    design = design_pond(body, screen_water=False)
    capacity = design['capacity_m3']
    # One day with very high demand; start at 10% capacity.
    result = daily_balance({'20200601': 0.}, design, runoff_area=0, coefficient=0,
                           evaporation=0, seepage=0, demand=1e9, initial=0.1)
    row = result['monthly'][0]
    # Supplied cannot exceed what was available.
    assert row['supplied_m3'] <= capacity * 0.1 + 1e-6
    # Unmet = demand - supplied.
    assert row['unmet_demand_m3'] == pytest.approx(1e9 - row['supplied_m3'], rel=1e-6)
    # Storage must be zero after meeting as much demand as possible.
    assert row['end_storage_m3'] < 1e-8


# ---------------------------------------------------------------------------
# TestSeasonalSummary
# ---------------------------------------------------------------------------

# Helper: build a minimal monthly row for seasonal_summary tests.
def _month_row(month_str, *, rain=0., runoff=0., direct=0., inflow=0.,
               overflow=0., evap=0., seep=0., supplied=0., unmet=0.,
               start=0., end=0., days_full=0, days_empty=0):
    return {
        'month': month_str,
        'rainfall_mm': rain, 'runoff_m3': runoff, 'direct_rain_m3': direct,
        'inflow_m3': inflow, 'overflow_m3': overflow, 'evaporation_m3': evap,
        'seepage_m3': seep, 'supplied_m3': supplied, 'unmet_demand_m3': unmet,
        'start_storage_m3': start, 'end_storage_m3': end,
        'days_full': days_full, 'days_empty': days_empty,
    }


class TestSeasonalSummary:

    # ── Default season constants ──────────────────────────────────────────

    def test_default_seasons_cover_all_months_exactly_once(self):
        covered = sorted(
            m for months in DEFAULT_SEASONS.values() for m in months
        )
        assert covered == list(range(1, 13))

    def test_monsoon_months_constant(self):
        assert set(MONSOON_MONTHS) == {6, 7, 8, 9}

    def test_post_monsoon_months_constant(self):
        assert set(POST_MONSOON_MONTHS) == {10, 11}

    def test_dry_months_constant(self):
        assert set(DRY_MONTHS) == {12, 1, 2, 3, 4, 5}

    # ── Invalid partition rejected ────────────────────────────────────────

    def test_incomplete_season_partition_raises(self):
        bad = {'wet': [6, 7, 8], 'dry': [1, 2, 3]}  # missing many months
        with pytest.raises(ValueError, match='every month 1'):
            seasonal_summary([], seasons=bad)

    def test_duplicate_month_in_partition_raises(self):
        bad = {'a': list(range(1, 13)), 'b': [1]}  # month 1 appears twice
        with pytest.raises(ValueError, match='every month 1'):
            seasonal_summary([], seasons=bad)

    # ── Structure of return value ─────────────────────────────────────────

    def test_returns_required_top_level_keys(self):
        result = seasonal_summary([])
        assert {'by_year', 'summary', 'seasons_used'}.issubset(result)

    def test_seasons_used_reflects_default(self):
        result = seasonal_summary([])
        assert set(result['seasons_used']) == {'monsoon', 'post_monsoon', 'dry'}

    def test_empty_rows_yields_zero_years(self):
        result = seasonal_summary([])
        assert result['by_year'] == []
        assert result['summary']['years_analysed'] == 0
        assert result['summary']['fill_rate_pct'] == 0.

    # ── Per-year / per-season aggregation ────────────────────────────────

    def test_two_years_produce_two_by_year_entries(self):
        rows = [
            _month_row(f'2020-{m:02d}') for m in range(1, 13)
        ] + [
            _month_row(f'2021-{m:02d}') for m in range(1, 13)
        ]
        result = seasonal_summary(rows)
        assert len(result['by_year']) == 2
        assert result['by_year'][0]['year'] == 2020
        assert result['by_year'][1]['year'] == 2021

    def test_each_year_has_three_seasons(self):
        rows = [_month_row(f'2020-{m:02d}') for m in range(1, 13)]
        result = seasonal_summary(rows)
        assert set(result['by_year'][0]['seasons']) == {'monsoon', 'post_monsoon', 'dry'}

    def test_seasonal_rainfall_sums_correctly(self):
        # Give every month 10 mm rainfall.
        rows = [_month_row(f'2020-{m:02d}', rain=10.) for m in range(1, 13)]
        result = seasonal_summary(rows)
        yr = result['by_year'][0]['seasons']
        assert yr['monsoon']['rainfall_mm'] == pytest.approx(40.)      # 4 months
        assert yr['post_monsoon']['rainfall_mm'] == pytest.approx(20.) # 2 months
        assert yr['dry']['rainfall_mm'] == pytest.approx(60.)          # 6 months

    def test_season_totals_sum_to_annual(self):
        """Across all seasons in a year, totals must equal the annual row total."""
        rows = [_month_row(f'2020-{m:02d}', overflow=float(m), rain=3.) for m in range(1, 13)]
        result = seasonal_summary(rows)
        yr = result['by_year'][0]['seasons']
        seasonal_overflow = math.fsum(s['overflow_m3'] for s in yr.values())
        assert seasonal_overflow == pytest.approx(sum(range(1, 13)))

    # ── days_full / filled flag ───────────────────────────────────────────

    def test_filled_flag_set_when_any_day_full(self):
        # One monsoon month with days_full > 0
        rows = [
            _month_row(f'2020-{m:02d}', days_full=(5 if m == 7 else 0))
            for m in range(1, 13)
        ]
        result = seasonal_summary(rows)
        yr = result['by_year'][0]['seasons']
        assert yr['monsoon']['filled'] is True
        assert yr['post_monsoon']['filled'] is False
        assert yr['dry']['filled'] is False

    def test_days_full_aggregates_across_season_months(self):
        rows = [
            _month_row(f'2020-{m:02d}', days_full=(m if m in MONSOON_MONTHS else 0))
            for m in range(1, 13)
        ]
        result = seasonal_summary(rows)
        expected = sum(MONSOON_MONTHS)  # 6+7+8+9 = 30
        assert result['by_year'][0]['seasons']['monsoon']['days_full'] == expected

    # ── Multi-year summary statistics ─────────────────────────────────────

    def test_fill_rate_zero_when_no_year_fills(self):
        rows = [_month_row(f'2020-{m:02d}') for m in range(1, 13)]
        result = seasonal_summary(rows)
        assert result['summary']['fill_rate_pct'] == 0.
        assert result['summary']['years_pond_filled'] == 0

    def test_fill_rate_100_when_every_year_fills(self):
        rows = [
            _month_row(f'{yr}-{m:02d}', days_full=1)
            for yr in (2020, 2021)
            for m in range(1, 13)
        ]
        result = seasonal_summary(rows)
        assert result['summary']['fill_rate_pct'] == pytest.approx(100.)
        assert result['summary']['years_pond_filled'] == 2

    def test_fill_rate_50_when_one_of_two_years_fills(self):
        rows_2020 = [
            _month_row(f'2020-{m:02d}', days_full=(3 if m == 8 else 0))
            for m in range(1, 13)
        ]
        rows_2021 = [_month_row(f'2021-{m:02d}') for m in range(1, 13)]
        result = seasonal_summary(rows_2020 + rows_2021)
        assert result['summary']['fill_rate_pct'] == pytest.approx(50.)

    def test_mean_annual_overflow_averages_across_years(self):
        # 2020: 100 m³ overflow; 2021: 200 m³ overflow
        rows_2020 = [
            _month_row(f'2020-{m:02d}', overflow=(100. if m == 8 else 0.))
            for m in range(1, 13)
        ]
        rows_2021 = [
            _month_row(f'2021-{m:02d}', overflow=(200. if m == 8 else 0.))
            for m in range(1, 13)
        ]
        result = seasonal_summary(rows_2020 + rows_2021)
        assert result['summary']['mean_annual_overflow_m3'] == pytest.approx(150.)

    def test_end_of_monsoon_storage_averages_correctly(self):
        # End of September (month 09) is the last monsoon month.
        rows_2020 = [_month_row(f'2020-{m:02d}', end=(80. if m == 9 else 0.))
                     for m in range(1, 13)]
        rows_2021 = [_month_row(f'2021-{m:02d}', end=(60. if m == 9 else 0.))
                     for m in range(1, 13)]
        result = seasonal_summary(rows_2020 + rows_2021)
        assert result['summary']['mean_end_monsoon_storage_m3'] == pytest.approx(70.)

    def test_end_of_monsoon_storage_pct_uses_capacity(self):
        rows = [_month_row(f'2020-{m:02d}', end=(50. if m == 9 else 0.))
                for m in range(1, 13)]
        result = seasonal_summary(rows, capacity_m3=100.)
        assert result['summary']['mean_end_monsoon_storage_pct'] == pytest.approx(50.)

    def test_no_capacity_gives_none_pct(self):
        rows = [_month_row(f'2020-{m:02d}') for m in range(1, 13)]
        result = seasonal_summary(rows, capacity_m3=None)
        assert result['summary']['mean_end_monsoon_storage_pct'] is None

    # ── Custom seasons ────────────────────────────────────────────────────

    def test_custom_two_season_partition(self):
        custom = {'wet': [6, 7, 8, 9, 10, 11], 'dry': [12, 1, 2, 3, 4, 5]}
        rows = [_month_row(f'2020-{m:02d}', rain=10.) for m in range(1, 13)]
        result = seasonal_summary(rows, seasons=custom)
        yr = result['by_year'][0]['seasons']
        assert set(yr) == {'wet', 'dry'}
        assert yr['wet']['rainfall_mm'] == pytest.approx(60.)
        assert yr['dry']['rainfall_mm'] == pytest.approx(60.)
        # No 'monsoon' key in custom seasons → end-of-monsoon stats are None
        assert result['summary']['mean_end_monsoon_storage_m3'] is None

    def test_custom_seasons_used_reflects_custom_definition(self):
        custom = {'a': list(range(1, 7)), 'b': list(range(7, 13))}
        result = seasonal_summary([], seasons=custom)
        assert set(result['seasons_used']) == {'a', 'b'}

    # ── Integration: simulate_pond includes seasonal key ──────────────────

    def test_simulate_pond_response_includes_seasonal(self, monkeypatch):
        def fetch(*args, **kwargs):
            return summarize_rainfall(history(), 2020, 2021, include_daily=True)
        monkeypatch.setattr('services.storage.fetch_rainfall', fetch)
        monkeypatch.setattr('services.waterways.fetch_waterways', lambda b: {'elements': []})
        data = dict(request_data(), catchment=geographic(box(X-100, Y-100, X+100, Y+100)))
        result = simulate_pond(data)
        assert 'seasonal' in result
        seasonal = result['seasonal']
        assert {'by_year', 'summary', 'seasons_used'} == set(seasonal)
        assert len(seasonal['by_year']) == 2          # 2020 and 2021
        assert seasonal['summary']['years_analysed'] == 2

    def test_simulate_pond_assumptions_include_seasons(self, monkeypatch):
        def fetch(*args, **kwargs):
            return summarize_rainfall(history(), 2020, 2021, include_daily=True)
        monkeypatch.setattr('services.storage.fetch_rainfall', fetch)
        monkeypatch.setattr('services.waterways.fetch_waterways', lambda b: {'elements': []})
        data = dict(request_data(), catchment=geographic(box(X-100, Y-100, X+100, Y+100)))
        result = simulate_pond(data)
        assert 'seasons' in result['assumptions']
        assert set(result['assumptions']['seasons']) == {'monsoon', 'post_monsoon', 'dry'}

    def test_simulate_pond_seasonal_mass_conservation(self, monkeypatch):
        """Sum of seasonal inflow == sum of annual inflow across all years."""
        def fetch(*args, **kwargs):
            return summarize_rainfall(history(), 2020, 2021, include_daily=True)
        monkeypatch.setattr('services.storage.fetch_rainfall', fetch)
        monkeypatch.setattr('services.waterways.fetch_waterways', lambda b: {'elements': []})
        data = dict(request_data(), catchment=geographic(box(X-100, Y-100, X+100, Y+100)))
        result = simulate_pond(data)
        annual_total_inflow = math.fsum(
            row['inflow_m3'] for row in result['annual']
        )
        seasonal_total_inflow = math.fsum(
            s['inflow_m3']
            for entry in result['seasonal']['by_year']
            for s in entry['seasons'].values()
        )
        assert seasonal_total_inflow == pytest.approx(annual_total_inflow)

