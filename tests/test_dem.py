"""
Tests: test_dem

Unit tests for analysis/dem.py

Synthetic terrain: concentric circular contours forming a dome.
No dependency on sample KML coordinates.
"""

import math
import pytest
import numpy as np

from analysis.dem import generate_dem, DEMGenerationError


# ---------------------------------------------------------------------------
# Synthetic data builder
# ---------------------------------------------------------------------------

def _dome_contours(
    n_levels: int = 5,
    base_elev: float = 270.0,
    interval: float = 5.0,
    cx: float = 1000.0,
    cy: float = 1000.0,
    ring_spacing: float = 150.0,
) -> list:
    """
    Concentric circular contours forming a dome:
      - outermost ring → lowest elevation
      - innermost ring → highest elevation
    All coordinates are already in projected metres.
    """
    contours = []
    for level in range(n_levels):
        elev = base_elev + level * interval
        radius = (n_levels - level) * ring_spacing
        n_pts = max(24, int(2 * math.pi * radius / 5))
        proj = [
            [cx + radius * math.cos(2 * math.pi * i / n_pts),
             cy + radius * math.sin(2 * math.pi * i / n_pts)]
            for i in range(n_pts)
        ]
        proj.append(proj[0])   # close ring
        contours.append({
            "id": level,
            "elevation": elev,
            "coordinates": [[0.0, 0.0]] * len(proj),   # dummy lon/lat
            "projected_coordinates": proj,
        })
    return contours


DOME = _dome_contours()


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------

class TestDEMStructure:

    def test_returns_dict(self):
        result = generate_dem(DOME)
        assert isinstance(result, dict)

    def test_required_keys(self):
        result = generate_dem(DOME)
        required = {"dem", "x_coords", "y_coords", "resolution_m",
                    "shape", "bounds", "nan_fraction", "elevation_min", "elevation_max"}
        assert required.issubset(result.keys())

    def test_dem_is_ndarray(self):
        result = generate_dem(DOME)
        assert isinstance(result["dem"], np.ndarray)

    def test_dem_is_2d(self):
        result = generate_dem(DOME)
        assert result["dem"].ndim == 2

    def test_shape_tuple_matches_dem(self):
        result = generate_dem(DOME)
        assert result["shape"] == result["dem"].shape

    def test_x_coords_1d(self):
        result = generate_dem(DOME)
        assert result["x_coords"].ndim == 1

    def test_y_coords_1d(self):
        result = generate_dem(DOME)
        assert result["y_coords"].ndim == 1

    def test_x_len_matches_dem_cols(self):
        result = generate_dem(DOME)
        assert len(result["x_coords"]) == result["dem"].shape[1]

    def test_y_len_matches_dem_rows(self):
        result = generate_dem(DOME)
        assert len(result["y_coords"]) == result["dem"].shape[0]

    def test_bounds_has_all_keys(self):
        result = generate_dem(DOME)
        assert set(result["bounds"].keys()) == {"min_x", "min_y", "max_x", "max_y"}


# ---------------------------------------------------------------------------
# Dimension / extent tests
# ---------------------------------------------------------------------------

class TestDEMDimensions:

    def test_grid_is_at_least_2x2(self):
        result = generate_dem(DOME)
        rows, cols = result["shape"]
        assert rows >= 2
        assert cols >= 2

    def test_x_coords_span_input_extent(self):
        result = generate_dem(DOME)
        b = result["bounds"]
        assert result["x_coords"].min() >= b["min_x"] - result["resolution_m"]
        assert result["x_coords"].max() <= b["max_x"] + result["resolution_m"]

    def test_y_coords_span_input_extent(self):
        result = generate_dem(DOME)
        b = result["bounds"]
        assert result["y_coords"].min() >= b["min_y"] - result["resolution_m"]
        assert result["y_coords"].max() <= b["max_y"] + result["resolution_m"]

    def test_resolution_is_positive(self):
        result = generate_dem(DOME)
        assert result["resolution_m"] > 0

    def test_explicit_resolution_used(self):
        result = generate_dem(DOME, resolution=10.0)
        assert result["resolution_m"] == 10.0

    def test_finer_resolution_gives_larger_grid(self):
        coarse = generate_dem(DOME, resolution=50.0)
        fine   = generate_dem(DOME, resolution=10.0)
        assert fine["dem"].size > coarse["dem"].size


# ---------------------------------------------------------------------------
# Elevation value tests
# ---------------------------------------------------------------------------

class TestDEMElevations:

    def test_elevation_within_contour_range(self):
        result = generate_dem(DOME)
        input_min = min(c["elevation"] for c in DOME)
        input_max = max(c["elevation"] for c in DOME)
        # Allow tiny float tolerance
        assert result["elevation_min"] >= input_min - 0.01
        assert result["elevation_max"] <= input_max + 0.01

    def test_elevation_min_less_than_max(self):
        result = generate_dem(DOME)
        assert result["elevation_min"] < result["elevation_max"]

    def test_no_nan_after_generation(self):
        """nearest-neighbour fill should leave zero NaN cells."""
        result = generate_dem(DOME)
        assert result["nan_fraction"] == 0.0
        assert not np.isnan(result["dem"]).any()

    def test_dem_values_are_finite(self):
        result = generate_dem(DOME)
        assert np.all(np.isfinite(result["dem"]))


# ---------------------------------------------------------------------------
# Independence / reusability tests
# ---------------------------------------------------------------------------

class TestDEMReusability:

    def test_different_location_gives_different_bounds(self):
        dome_a = _dome_contours(cx=1000.0, cy=1000.0)
        dome_b = _dome_contours(cx=5000.0, cy=5000.0)
        ra = generate_dem(dome_a)
        rb = generate_dem(dome_b)
        assert ra["bounds"]["min_x"] != rb["bounds"]["min_x"]

    def test_different_elevation_range(self):
        low  = _dome_contours(base_elev=200.0, interval=5.0)
        high = _dome_contours(base_elev=500.0, interval=5.0)
        rl = generate_dem(low)
        rh = generate_dem(high)
        assert rh["elevation_min"] > rl["elevation_max"] - 1

    def test_more_levels_does_not_crash(self):
        big = _dome_contours(n_levels=10, ring_spacing=80.0)
        result = generate_dem(big)
        assert result["dem"].size > 0

    def test_coarser_interval_gives_valid_dem(self):
        sparse = _dome_contours(n_levels=3, interval=10.0, ring_spacing=200.0)
        result = generate_dem(sparse)
        assert result["nan_fraction"] == 0.0


# ---------------------------------------------------------------------------
# Error / edge-case tests
# ---------------------------------------------------------------------------

class TestDEMErrors:

    def test_empty_contours_raises(self):
        with pytest.raises(DEMGenerationError, match="No projected contours"):
            generate_dem([])

    def test_missing_projected_coordinates_raises(self):
        bad = [{"id": 0, "elevation": 270.0, "coordinates": [[0, 0], [1, 1]]}]
        with pytest.raises(DEMGenerationError, match="projected_coordinates"):
            generate_dem(bad)

    def test_empty_projected_coordinates_raises(self):
        bad = [{"id": 0, "elevation": 270.0,
                "coordinates": [[0, 0]], "projected_coordinates": []}]
        with pytest.raises(DEMGenerationError, match="projected_coordinates"):
            generate_dem(bad)
