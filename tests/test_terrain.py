"""
Tests: test_terrain

Unit tests for analysis/terrain.py — covers:

  Happy paths
  ───────────
  - Correct contour count
  - Correct min / max elevation
  - Uniform contour interval derived correctly
  - Non-uniform interval detected
  - Correct total point count
  - Correct geographic bounds (min/max lon/lat)
  - contour_interval_uniform flag is True for uniform data
  - contour_interval_uniform flag is False for non-uniform data

  Validation / error paths
  ────────────────────────
  - Empty list raises TerrainValidationError
  - Contour missing elevation raises
  - Contour missing coordinates raises
  - Contour with only 1 point raises
  - Coordinate with longitude > 180 raises
  - Coordinate with longitude < -180 raises
  - Coordinate with latitude > 90 raises
  - Coordinate with latitude < -90 raises
  - Only 1 unique elevation level raises (cannot derive interval)
"""

import pytest
from analysis.terrain import analyze_contours, TerrainValidationError


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_contour(elevation: float, coords: list, cid: int = 0) -> dict:
    """Build a minimal contour dict."""
    return {"id": cid, "elevation": elevation, "coordinates": coords}


def _simple_coords(n: int = 3, lon_base: float = 81.0, lat_base: float = 21.0) -> list:
    """Return n distinct [lon, lat] pairs."""
    return [[round(lon_base + i * 0.001, 6), round(lat_base + i * 0.001, 6)] for i in range(n)]


# A clean dataset with 3 contours at uniform 5 m intervals
UNIFORM_DATASET = [
    _make_contour(270.0, _simple_coords(4, 81.0, 21.0), cid=0),
    _make_contour(275.0, _simple_coords(5, 81.1, 21.1), cid=1),
    _make_contour(280.0, _simple_coords(3, 81.2, 21.2), cid=2),
]


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestBasicMetrics:

    def test_contour_count(self):
        result = analyze_contours(UNIFORM_DATASET)
        assert result["contour_count"] == 3

    def test_min_elevation(self):
        result = analyze_contours(UNIFORM_DATASET)
        assert result["min_elevation_m"] == 270.0

    def test_max_elevation(self):
        result = analyze_contours(UNIFORM_DATASET)
        assert result["max_elevation_m"] == 280.0

    def test_total_points(self):
        # 4 + 5 + 3 = 12
        result = analyze_contours(UNIFORM_DATASET)
        assert result["total_points"] == 12

    def test_terrain_dict_has_all_keys(self):
        result = analyze_contours(UNIFORM_DATASET)
        expected = {
            "contour_count", "min_elevation_m", "max_elevation_m",
            "contour_interval_m", "contour_interval_uniform",
            "total_points", "bounds",
        }
        assert expected.issubset(result.keys())

    def test_bounds_dict_has_all_keys(self):
        result = analyze_contours(UNIFORM_DATASET)
        assert set(result["bounds"].keys()) == {"min_lon", "min_lat", "max_lon", "max_lat"}


class TestContourInterval:

    def test_uniform_interval_value(self):
        """5 m uniform interval is correctly derived."""
        result = analyze_contours(UNIFORM_DATASET)
        assert result["contour_interval_m"] == 5.0

    def test_uniform_interval_flag(self):
        """contour_interval_uniform is True for evenly spaced elevations."""
        result = analyze_contours(UNIFORM_DATASET)
        assert result["contour_interval_uniform"] is True

    def test_non_uniform_interval_flag(self):
        """contour_interval_uniform is False when gaps differ."""
        dataset = [
            _make_contour(270.0, _simple_coords(3, 81.0, 21.0)),
            _make_contour(275.0, _simple_coords(3, 81.1, 21.1)),
            _make_contour(283.0, _simple_coords(3, 81.2, 21.2)),  # 8 m gap
        ]
        result = analyze_contours(dataset)
        assert result["contour_interval_uniform"] is False

    def test_non_uniform_returns_dominant_interval(self):
        """
        The dominant (most frequent) interval is returned even when
        the dataset is non-uniform.
        """
        dataset = [
            _make_contour(270.0, _simple_coords(3, 81.0, 21.0)),
            _make_contour(275.0, _simple_coords(3, 81.1, 21.1)),
            _make_contour(280.0, _simple_coords(3, 81.2, 21.2)),
            _make_contour(285.0, _simple_coords(3, 81.3, 21.3)),
            _make_contour(295.0, _simple_coords(3, 81.4, 21.4)),  # 10 m gap — outlier
        ]
        result = analyze_contours(dataset)
        # Most common gap is 5.0 m (appears 3 times vs 1 time for 10 m)
        assert result["contour_interval_m"] == 5.0
        assert result["contour_interval_uniform"] is False

    def test_two_contours_gives_exact_interval(self):
        """With exactly two contours the interval equals the elevation difference."""
        dataset = [
            _make_contour(260.0, _simple_coords(3, 81.0, 21.0)),
            _make_contour(265.0, _simple_coords(3, 81.1, 21.1)),
        ]
        result = analyze_contours(dataset)
        assert result["contour_interval_m"] == 5.0
        assert result["contour_interval_uniform"] is True


class TestGeographicBounds:

    def test_min_lon(self):
        result = analyze_contours(UNIFORM_DATASET)
        # Smallest lon is 81.0 (first point of first contour)
        assert result["bounds"]["min_lon"] == pytest.approx(81.0, abs=0.01)

    def test_max_lat(self):
        result = analyze_contours(UNIFORM_DATASET)
        # Largest lat is in the third contour (base 21.2)
        assert result["bounds"]["max_lat"] > 21.1

    def test_bounds_are_finite(self):
        result = analyze_contours(UNIFORM_DATASET)
        b = result["bounds"]
        assert b["min_lon"] <= b["max_lon"]
        assert b["min_lat"] <= b["max_lat"]

    def test_single_point_per_contour_bounds(self):
        """Bounds work correctly even with the minimum 2 points per contour."""
        dataset = [
            _make_contour(270.0, [[80.0, 20.0], [80.5, 20.5]]),
            _make_contour(275.0, [[81.0, 21.0], [81.5, 21.5]]),
        ]
        result = analyze_contours(dataset)
        assert result["bounds"]["min_lon"] == 80.0
        assert result["bounds"]["max_lon"] == 81.5
        assert result["bounds"]["min_lat"] == 20.0
        assert result["bounds"]["max_lat"] == 21.5


# ---------------------------------------------------------------------------
# Validation / error tests
# ---------------------------------------------------------------------------

class TestValidationErrors:

    def test_empty_list_raises(self):
        with pytest.raises(TerrainValidationError, match="empty"):
            analyze_contours([])

    def test_missing_elevation_raises(self):
        bad = [{"id": 0, "elevation": None, "coordinates": [[81.0, 21.0], [81.1, 21.1]]}]
        with pytest.raises(TerrainValidationError, match="missing an elevation"):
            analyze_contours(bad)

    def test_missing_coordinates_raises(self):
        bad = [_make_contour(270.0, [])]
        with pytest.raises(TerrainValidationError, match="no coordinates"):
            analyze_contours(bad)

    def test_single_point_contour_raises(self):
        """A contour with only 1 point is geometrically useless."""
        bad = [_make_contour(270.0, [[81.0, 21.0]])]
        with pytest.raises(TerrainValidationError, match="at least"):
            analyze_contours(bad)

    def test_only_one_elevation_level_raises(self):
        """At least 2 distinct elevations are needed to derive an interval."""
        dataset = [
            _make_contour(270.0, _simple_coords(3, 81.0, 21.0)),
            _make_contour(270.0, _simple_coords(3, 81.1, 21.1)),  # duplicate elevation
        ]
        with pytest.raises(TerrainValidationError, match="Insufficient elevation"):
            analyze_contours(dataset)

    def test_longitude_too_large_raises(self):
        bad = [_make_contour(270.0, [[200.0, 21.0], [81.1, 21.1]])]
        with pytest.raises(TerrainValidationError, match="longitude"):
            analyze_contours(bad)

    def test_longitude_too_small_raises(self):
        bad = [_make_contour(270.0, [[-190.0, 21.0], [81.1, 21.1]])]
        with pytest.raises(TerrainValidationError, match="longitude"):
            analyze_contours(bad)

    def test_latitude_too_large_raises(self):
        bad = [_make_contour(270.0, [[81.0, 95.0], [81.1, 21.1]])]
        with pytest.raises(TerrainValidationError, match="latitude"):
            analyze_contours(bad)

    def test_latitude_too_small_raises(self):
        bad = [_make_contour(270.0, [[81.0, -91.0], [81.1, 21.1]])]
        with pytest.raises(TerrainValidationError, match="latitude"):
            analyze_contours(bad)

    def test_non_numeric_elevation_raises(self):
        bad = [{"id": 0, "elevation": "high", "coordinates": [[81.0, 21.0], [81.1, 21.1]]}]
        with pytest.raises(TerrainValidationError, match="non-numeric elevation"):
            analyze_contours(bad)
