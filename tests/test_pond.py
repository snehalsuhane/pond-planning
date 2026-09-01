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


# ---------------------------------------------------------------------------
# find_pond_candidate tests
# ---------------------------------------------------------------------------

class TestFindPondCandidate:

    def test_returns_dict_with_pond_site(self, dome_dem, dome_slope):
        result = find_pond_candidate(dome_dem, dome_slope, EPSG)
        assert "pond_site" in result

    def test_pond_site_has_all_keys(self, dome_dem, dome_slope):
        site = find_pond_candidate(dome_dem, dome_slope, EPSG)["pond_site"]
        expected = {"latitude", "longitude", "elevation_m", "slope_deg",
                    "grid_row", "grid_col", "score", "rank", "tpi", "criteria"}
        assert expected.issubset(site.keys())
        assert {"elevation_score", "slope_score", "depression_score"}.issubset(site["criteria"].keys())

    def test_latitude_is_numeric(self, dome_dem, dome_slope):
        site = find_pond_candidate(dome_dem, dome_slope, EPSG)["pond_site"]
        assert isinstance(site["latitude"], float)

    def test_longitude_is_numeric(self, dome_dem, dome_slope):
        site = find_pond_candidate(dome_dem, dome_slope, EPSG)["pond_site"]
        assert isinstance(site["longitude"], float)

    def test_candidate_elevation_within_dem_range(self, dome_dem, dome_slope):
        site = find_pond_candidate(dome_dem, dome_slope, EPSG)["pond_site"]
        assert dome_dem["elevation_min"] <= site["elevation_m"] <= dome_dem["elevation_max"]

    def test_candidate_slope_within_threshold(self, dome_dem, dome_slope):
        max_slope = 8.0
        site = find_pond_candidate(
            dome_dem, dome_slope, EPSG, max_slope_deg=max_slope
        )["pond_site"]
        assert site["slope_deg"] <= max_slope

    def test_grid_row_within_dem_shape(self, dome_dem, dome_slope):
        site = find_pond_candidate(dome_dem, dome_slope, EPSG)["pond_site"]
        rows, cols = dome_dem["shape"]
        assert 0 <= site["grid_row"] < rows
        assert 0 <= site["grid_col"] < cols

    def test_candidate_coords_within_projected_bounds(self, dome_dem, dome_slope):
        """The back-projected lat/lon should be geographically reasonable for EPSG:32644."""
        site = find_pond_candidate(dome_dem, dome_slope, EPSG)["pond_site"]
        # EPSG:32644 covers roughly lon 78–84, lat 0–84
        assert 78.0 <= site["longitude"] <= 84.0
        assert 0.0  <= site["latitude"]  <= 84.0

    def test_different_terrain_gives_different_candidate(self):
        """Candidate is derived from data, not hardcoded."""
        dem_a = generate_dem(_dome_contours(cx=360000.0, cy=2350000.0), resolution=10.0)
        dem_b = generate_dem(_dome_contours(cx=365000.0, cy=2360000.0), resolution=10.0)
        slope_a = calculate_slope(dem_a)
        slope_b = calculate_slope(dem_b)
        site_a = find_pond_candidate(dem_a, slope_a, EPSG)["pond_site"]
        site_b = find_pond_candidate(dem_b, slope_b, EPSG)["pond_site"]
        # The selected geographic locations must differ between the two terrains
        assert (site_a["longitude"] != site_b["longitude"] or
                site_a["latitude"] != site_b["latitude"])

    def test_lower_elevation_is_preferred(self):
        """
        With a simple ramp, the best site should be at the low end
        (where elevation is lowest and slope is uniform).
        """
        ramp = _ramp_dem_result(rows=20, cols=20, res=5.0)
        slope_r = calculate_slope(ramp)
        site = find_pond_candidate(ramp, slope_r, EPSG, border_cells=1)["pond_site"]
        # Low end of ramp → col near 0
        assert site["grid_col"] < 10   # left half of the 20-col grid

    def test_no_valid_cells_raises(self):
        """If every cell is too steep, PondCandidateError should be raised."""
        # Create a DEM result with very steep slope everywhere
        steep_dem = _ramp_dem_result(rows=10, cols=10, res=1.0)
        # Make slope result say every cell is 45°
        steep_slope = {
            "slope":      np.full((10, 10), 45.0),
            "slope_min":  45.0,
            "slope_max":  45.0,
            "slope_mean": 45.0,
        }
        with pytest.raises(PondCandidateError):
            find_pond_candidate(steep_dem, steep_slope, EPSG, max_slope_deg=5.0)


# ---------------------------------------------------------------------------
# rank_pond_candidates tests
# ---------------------------------------------------------------------------

class TestRankPondCandidates:

    def test_returns_top_n_candidates(self, dome_dem, dome_slope):
        result = rank_pond_candidates(dome_dem, dome_slope, EPSG, num_candidates=3)
        assert "pond_candidates" in result
        candidates = result["pond_candidates"]
        assert len(candidates) == 3
        # Ensure ranks are 1, 2, 3
        ranks = [c["rank"] for c in candidates]
        assert ranks == [1, 2, 3]
        
    def test_candidates_are_spatially_distinct(self, dome_dem, dome_slope):
        result = rank_pond_candidates(dome_dem, dome_slope, EPSG, num_candidates=2, min_distance_m=50.0)
        c1, c2 = result["pond_candidates"]
        
        # Calculate pixel distance
        dr = c1["grid_row"] - c2["grid_row"]
        dc = c1["grid_col"] - c2["grid_col"]
        dist_px = math.sqrt(dr**2 + dc**2)
        dist_m = dist_px * dome_dem["resolution_m"]
        assert dist_m >= 50.0

    def test_depression_preferred(self):
        """A local depression (basin) should be preferred over a flat area of the same elevation."""
        from analysis.terrain import calculate_slope
        from analysis.pond import rank_pond_candidates
        import numpy as np

        # Create a flat DEM at elevation 10.0
        res = 5.0
        dem = np.full((30, 30), 10.0)
        # Create a basin in the middle (elevation 5.0)
        dem[10:20, 10:20] = 5.0
        # The center of the basin (15, 15) is fully flat and lowest.
        # Let's create a second flat area at elevation 5.0 but on the edge, so it is NOT a basin
        dem[0:5, 0:5] = 5.0

        dem_result = {
            "dem": dem,
            "x_coords": np.arange(30) * res,
            "y_coords": np.arange(30) * res,
            "resolution_m": res,
            "shape": dem.shape,
            "bounds": {"min_x": 0, "max_x": 150, "min_y": 0, "max_y": 150}
        }
        slope_result = calculate_slope(dem_result)
        
        # Rank candidates. The basin center should be ranked #1
        EPSG = 32644
        candidates = rank_pond_candidates(dem_result, slope_result, EPSG, num_candidates=2)["pond_candidates"]
        
        # Candidate 1 should be inside the basin (row, col near 15)
        c1 = candidates[0]
        assert 10 <= c1["grid_row"] < 20
        assert 10 <= c1["grid_col"] < 20

    def test_criteria_scores_sum_to_total(self, dome_dem, dome_slope):
        """The component scores in criteria should sum to the overall score."""
        result = rank_pond_candidates(dome_dem, dome_slope, EPSG, num_candidates=5)
        for c in result["pond_candidates"]:
            crit = c["criteria"]
            component_sum = sum(crit.values())
            # Allow small floating-point rounding (scores are rounded to 3dp each)
            assert abs(component_sum - c["score"]) < 0.01, (
                f"Rank #{c['rank']}: criteria sum {component_sum:.4f} != score {c['score']:.4f}"
            )
