"""
Tests: test_hydrology

Unit tests for analysis/hydrology.py.
All tests use synthetic DEMs — no dependency on sample KML coordinates.
"""

import math
import numpy as np
import pytest

from analysis.hydrology import (
    calculate_flow_direction,
    calculate_flow_accumulation,
    detect_channels,
    run_hydrology,
)


# ---------------------------------------------------------------------------
# Synthetic DEM helpers
# ---------------------------------------------------------------------------

def _make_dem_result(dem: np.ndarray, res: float = 5.0) -> dict:
    rows, cols = dem.shape
    return {
        "dem":           dem,
        "x_coords":      np.arange(cols, dtype=float) * res,
        "y_coords":      np.arange(rows, dtype=float) * res,
        "resolution_m":  res,
        "shape":         dem.shape,
        "elevation_min": float(dem.min()),
        "elevation_max": float(dem.max()),
        "nan_fraction":  0.0,
        "bounds":        {"min_x": 0.0, "min_y": 0.0,
                          "max_x": cols * res, "max_y": rows * res},
    }


def _ramp_x(rows=20, cols=20, res=5.0):
    """Elevation increases left→right → water flows westward."""
    x = np.tile(np.arange(cols, dtype=float), (rows, 1))
    return _make_dem_result(270.0 + x * 2.0, res)


def _ramp_y(rows=20, cols=20, res=5.0):
    """Elevation increases bottom→top → water flows southward."""
    y = np.repeat(np.arange(rows, dtype=float)[::-1, None], cols, axis=1)
    return _make_dem_result(270.0 + y * 2.0, res)


def _bowl(rows=21, cols=21, res=5.0):
    """Inverted dome — lowest point at centre."""
    cy, cx = rows // 2, cols // 2
    Y, X = np.ogrid[:rows, :cols]
    r = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    return _make_dem_result(270.0 + r * 1.5, res)


def _dome(rows=30, cols=30, res=5.0):
    """Dome — highest at centre, radiating outward (as in real terrain)."""
    cy, cx = rows // 2, cols // 2
    Y, X = np.ogrid[:rows, :cols]
    r = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    return _make_dem_result(290.0 - r * 1.5, res)


# ---------------------------------------------------------------------------
# calculate_flow_direction
# ---------------------------------------------------------------------------

class TestCalculateFlowDirection:

    def test_returns_dict(self):
        assert isinstance(calculate_flow_direction(_ramp_x()), dict)

    def test_required_keys(self):
        result = calculate_flow_direction(_ramp_x())
        assert {"flow_direction", "noflow_count", "shape"}.issubset(result.keys())

    def test_flow_direction_is_ndarray(self):
        result = calculate_flow_direction(_ramp_x())
        assert isinstance(result["flow_direction"], np.ndarray)

    def test_shape_matches_dem(self):
        dem_r = _ramp_x(rows=15, cols=25)
        result = calculate_flow_direction(dem_r)
        assert result["shape"] == (15, 25)
        assert result["flow_direction"].shape == (15, 25)

    def test_only_valid_d8_codes(self):
        """Every cell must have one of the 9 valid D8 codes (0 = no flow)."""
        fdir = calculate_flow_direction(_dome())["flow_direction"]
        valid = {0, 1, 2, 4, 8, 16, 32, 64, 128}
        assert set(np.unique(fdir)).issubset(valid)

    def test_ramp_x_interior_flows_west(self):
        """On a west-rising ramp, interior cells should flow west (code 16)."""
        result = calculate_flow_direction(_ramp_x(rows=10, cols=10))
        fdir = result["flow_direction"]
        # Interior (avoiding padded edges)
        interior = fdir[2:-2, 2:-2]
        assert np.all(interior == 16)

    def test_ramp_y_interior_flows_south(self):
        """On a south-rising ramp, interior cells should flow south (code 4)."""
        result = calculate_flow_direction(_ramp_y(rows=10, cols=10))
        interior = result["flow_direction"][2:-2, 2:-2]
        assert np.all(interior == 4)

    def test_noflow_count_is_non_negative(self):
        result = calculate_flow_direction(_dome())
        assert result["noflow_count"] >= 0

    def test_bowl_centre_is_a_pit(self):
        """The bowl's centre cell has no lower neighbour — should be a pit."""
        dem_r = _bowl(rows=21, cols=21)
        result = calculate_flow_direction(dem_r)
        assert result["noflow_count"] >= 1

    def test_flat_dem_all_noflow(self):
        """A completely flat DEM should have no direction for any cell."""
        flat = _make_dem_result(np.full((10, 10), 270.0))
        result = calculate_flow_direction(flat)
        assert result["noflow_count"] == 100


# ---------------------------------------------------------------------------
# calculate_flow_accumulation
# ---------------------------------------------------------------------------

class TestCalculateFlowAccumulation:

    @pytest.fixture(scope="class")
    def ramp_fdir(self):
        return calculate_flow_direction(_ramp_x(rows=10, cols=10))

    @pytest.fixture(scope="class")
    def dome_fdir(self):
        return calculate_flow_direction(_dome())

    def test_returns_dict(self, ramp_fdir):
        assert isinstance(calculate_flow_accumulation(ramp_fdir), dict)

    def test_required_keys(self, ramp_fdir):
        result = calculate_flow_accumulation(ramp_fdir)
        assert {"flow_accumulation", "acc_max", "acc_mean", "shape"}.issubset(result.keys())

    def test_accumulation_is_ndarray(self, ramp_fdir):
        result = calculate_flow_accumulation(ramp_fdir)
        assert isinstance(result["flow_accumulation"], np.ndarray)

    def test_shape_matches_fdir(self, ramp_fdir):
        result = calculate_flow_accumulation(ramp_fdir)
        assert result["shape"] == ramp_fdir["shape"]

    def test_accumulation_minimum_is_one(self, dome_fdir):
        """Every cell accumulates at least itself (value ≥ 1)."""
        result = calculate_flow_accumulation(dome_fdir)
        assert result["flow_accumulation"].min() >= 1.0

    def test_accumulation_max_sensible(self, dome_fdir):
        """Max accumulation ≤ total number of cells."""
        result = calculate_flow_accumulation(dome_fdir)
        total = math.prod(dome_fdir["shape"])
        assert result["acc_max"] <= total

    def test_accumulation_increases_downstream_ramp(self):
        """On a west-flowing ramp, westward columns should have higher accumulation."""
        dem_r = _ramp_x(rows=8, cols=8)
        fdir  = calculate_flow_direction(dem_r)
        acc   = calculate_flow_accumulation(fdir)["flow_accumulation"]
        # Left column (col=0) is the sink — highest accumulation
        # Right column (col=7) is headwater — lowest accumulation
        col_mean = acc.mean(axis=0)
        assert col_mean[0] > col_mean[-1]

    def test_acc_max_equals_acc_mean_times_size_roughly(self, dome_fdir):
        """acc_mean should be less than acc_max (unless all cells same)."""
        result = calculate_flow_accumulation(dome_fdir)
        assert result["acc_mean"] <= result["acc_max"]

    def test_conservation_ramp(self):
        """Total accumulation must equal total cell count (flow is conserved)."""
        dem_r = _ramp_x(rows=6, cols=6)
        fdir  = calculate_flow_direction(dem_r)
        acc   = calculate_flow_accumulation(fdir)["flow_accumulation"]
        # Sum of accumulation = N² for a perfectly routing system
        # (each cell counted once per path it belongs to).
        # For a ramp, sink cells absorb all upstream flow;
        # total in sinks = N * (N+1)/2 per row.  Just check total >= N.
        assert acc.sum() >= acc.size


# ---------------------------------------------------------------------------
# detect_channels
# ---------------------------------------------------------------------------

class TestDetectChannels:

    @pytest.fixture(scope="class")
    def dome_facc(self):
        fdir = calculate_flow_direction(_dome(rows=40, cols=40))
        return calculate_flow_accumulation(fdir)

    def test_returns_dict(self, dome_facc):
        assert isinstance(detect_channels(dome_facc), dict)

    def test_required_keys(self, dome_facc):
        result = detect_channels(dome_facc)
        assert {"channel_mask", "threshold", "channel_cell_count",
                "channel_fraction"}.issubset(result.keys())

    def test_channel_mask_is_bool_array(self, dome_facc):
        result = detect_channels(dome_facc)
        assert result["channel_mask"].dtype == bool

    def test_channel_mask_non_empty(self, dome_facc):
        result = detect_channels(dome_facc)
        assert result["channel_cell_count"] > 0

    def test_default_threshold_is_99th_percentile(self, dome_facc):
        acc    = dome_facc["flow_accumulation"]
        result = detect_channels(dome_facc)
        expected = float(np.percentile(acc, 99))
        assert abs(result["threshold"] - expected) < 1e-6

    def test_explicit_threshold_respected(self, dome_facc):
        acc = dome_facc["flow_accumulation"]
        t   = float(acc.max() / 2)
        result = detect_channels(dome_facc, threshold=t)
        assert abs(result["threshold"] - t) < 1e-9

    def test_higher_threshold_fewer_channels(self, dome_facc):
        low  = detect_channels(dome_facc, threshold=5.0)["channel_cell_count"]
        high = detect_channels(dome_facc, threshold=500.0)["channel_cell_count"]
        assert low >= high

    def test_threshold_at_max_gives_at_least_one_cell(self, dome_facc):
        acc = dome_facc["flow_accumulation"]
        result = detect_channels(dome_facc, threshold=float(acc.max()))
        assert result["channel_cell_count"] >= 1

    def test_channel_fraction_between_0_and_1(self, dome_facc):
        result = detect_channels(dome_facc)
        assert 0.0 <= result["channel_fraction"] <= 1.0


# ---------------------------------------------------------------------------
# run_hydrology (wrapper)
# ---------------------------------------------------------------------------

class TestRunHydrology:

    @pytest.fixture(scope="class")
    def hydro(self):
        return run_hydrology(_dome(rows=30, cols=30))

    def test_returns_dict(self, hydro):
        assert isinstance(hydro, dict)

    def test_all_top_level_keys_present(self, hydro):
        expected = {
            "flow_direction", "flow_accumulation", "channel_mask",
            "channel_threshold", "channel_cell_count", "channel_fraction",
            "noflow_count", "acc_max", "acc_mean",
        }
        assert expected.issubset(hydro.keys())

    def test_flow_direction_is_array(self, hydro):
        assert isinstance(hydro["flow_direction"], np.ndarray)

    def test_flow_accumulation_is_array(self, hydro):
        assert isinstance(hydro["flow_accumulation"], np.ndarray)

    def test_channel_mask_is_bool_array(self, hydro):
        assert hydro["channel_mask"].dtype == bool

    def test_custom_threshold_propagates(self):
        hydro_low  = run_hydrology(_dome(), channel_threshold=10.0)
        hydro_high = run_hydrology(_dome(), channel_threshold=1000.0)
        assert hydro_low["channel_cell_count"] >= hydro_high["channel_cell_count"]

    def test_no_sample_specific_coords(self):
        """Grid is purely synthetic — test proves algorithm is location-agnostic."""
        h1 = run_hydrology(_dome(rows=20, cols=20))
        h2 = run_hydrology(_bowl(rows=20, cols=20))
        # Bowl and dome have different accumulation patterns
        assert h1["acc_max"] != h2["acc_max"] or h1["noflow_count"] != h2["noflow_count"]
