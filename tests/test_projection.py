"""
Tests: test_projection

Unit tests for utils/projection.py — covers:

  UTM zone selection
  ──────────────────
  - Correct EPSG for India / Northern hemisphere (your sample data)
  - Correct EPSG for Southern hemisphere
  - Zone boundary at prime meridian
  - Zone boundary at 180° antimeridian

  project_coordinates
  ───────────────────
  - Output is a list of [X, Y] pairs
  - X and Y are numeric floats
  - Altitude is NOT present (only 2 elements per pair)
  - Known geographic point projects to approximately correct metre values
  - Same input always produces the same output (deterministic)

  project_contours
  ────────────────
  - Returns (list, dict) tuple
  - Every contour gains 'projected_coordinates' key
  - Original 'coordinates' key is unchanged
  - Projected X/Y are numeric floats
  - Projected coordinate count matches original coordinate count
  - CRS info dict contains expected keys (epsg, name, unit)
  - CRS unit is 'metre'
  - Empty input raises ValueError
  - Code does not depend on specific input coordinates (tested with
    multiple geographic locations)
"""

import math
import pytest
from pyproj import Transformer

from utils.projection import (
    get_utm_epsg,
    project_coordinates,
    project_contours,
    _make_transformer,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_contour(elevation: float, coords: list, cid: int = 0) -> dict:
    return {"id": cid, "elevation": elevation, "coordinates": coords}


def _simple_contours(lon: float = 81.29, lat: float = 21.26) -> list:
    """Two-contour dataset centred on (lon, lat) — safe for any location."""
    return [
        _make_contour(270.0, [[lon, lat], [lon + 0.001, lat + 0.001]], cid=0),
        _make_contour(275.0, [[lon + 0.002, lat], [lon + 0.003, lat + 0.001]], cid=1),
    ]


# ---------------------------------------------------------------------------
# UTM zone / EPSG selection tests
# ---------------------------------------------------------------------------

class TestGetUtmEpsg:

    def test_india_northern_zone_44(self):
        """Sample data at ~81°E, ~21°N → UTM Zone 44N → EPSG 32644."""
        epsg = get_utm_epsg(81.29, 21.26)
        assert epsg == 32644

    def test_southern_hemisphere(self):
        """Negative latitude → southern hemisphere base 32700."""
        epsg = get_utm_epsg(81.29, -21.26)
        assert epsg == 32744      # Zone 44S

    def test_zone_1_at_antimeridian(self):
        """Longitude -179° → Zone 1."""
        epsg = get_utm_epsg(-179.0, 10.0)
        assert epsg == 32601

    def test_zone_30_near_prime_meridian(self):
        """Longitude -1° → Zone 30."""
        epsg = get_utm_epsg(-1.0, 51.5)
        assert epsg == 32630

    def test_zone_31_at_prime_meridian(self):
        """Longitude 0° → Zone 31."""
        epsg = get_utm_epsg(0.0, 51.5)
        assert epsg == 32631

    def test_zone_60_near_antimeridian(self):
        """Longitude 179° → Zone 60."""
        epsg = get_utm_epsg(179.0, 10.0)
        assert epsg == 32660

    def test_returns_integer(self):
        epsg = get_utm_epsg(81.29, 21.26)
        assert isinstance(epsg, int)


# ---------------------------------------------------------------------------
# project_coordinates tests
# ---------------------------------------------------------------------------

class TestProjectCoordinates:

    @pytest.fixture
    def transformer_44n(self):
        return _make_transformer(32644)

    def test_returns_list(self, transformer_44n):
        result = project_coordinates([[81.29, 21.26]], transformer_44n)
        assert isinstance(result, list)

    def test_output_length_matches_input(self, transformer_44n):
        coords = [[81.29, 21.26], [81.30, 21.27], [81.31, 21.28]]
        result = project_coordinates(coords, transformer_44n)
        assert len(result) == len(coords)

    def test_each_pair_has_two_elements(self, transformer_44n):
        result = project_coordinates([[81.29, 21.26]], transformer_44n)
        assert len(result[0]) == 2

    def test_x_and_y_are_floats(self, transformer_44n):
        result = project_coordinates([[81.29, 21.26]], transformer_44n)
        x, y = result[0]
        assert isinstance(x, float)
        assert isinstance(y, float)

    def test_x_in_metre_range_for_utm(self, transformer_44n):
        """
        UTM Easting (X) for any point is between 100 000 m and 900 000 m.
        This is a structural range check — does not depend on specific coords.
        """
        result = project_coordinates([[81.29, 21.26]], transformer_44n)
        x = result[0][0]
        assert 100_000 < x < 900_000, f"X={x} outside expected UTM easting range"

    def test_y_in_metre_range_for_utm_north(self, transformer_44n):
        """
        UTM Northing (Y) for Northern hemisphere is roughly 0–9 000 000 m.
        """
        result = project_coordinates([[81.29, 21.26]], transformer_44n)
        y = result[0][1]
        assert 0 < y < 9_000_000, f"Y={y} outside expected UTM northing range"

    def test_deterministic(self, transformer_44n):
        """Same input always produces the same output."""
        coords = [[81.286321, 21.263539]]
        r1 = project_coordinates(coords, transformer_44n)
        r2 = project_coordinates(coords, transformer_44n)
        assert r1 == r2

    def test_adjacent_points_differ(self, transformer_44n):
        """Two distinct lon/lat points must produce distinct X/Y values."""
        r = project_coordinates([[81.286321, 21.263539], [81.286400, 21.263518]], transformer_44n)
        assert r[0] != r[1]

    def test_metre_distance_order_of_magnitude(self, transformer_44n):
        """
        Two points ~0.001° apart in lon/lat should be roughly 100 m apart
        in projected space (at ~21°N, 1° lon ≈ 103 km, so 0.001° ≈ 103 m).
        """
        r = project_coordinates([[81.286321, 21.263539], [81.287321, 21.263539]], transformer_44n)
        dx = r[1][0] - r[0][0]
        # Should be ~103 m — check it's in the 80–130 m range
        assert 80 < abs(dx) < 130, f"dx={dx:.1f} m not in expected range"


# ---------------------------------------------------------------------------
# project_contours tests
# ---------------------------------------------------------------------------

class TestProjectContours:

    def test_returns_tuple(self):
        result = project_contours(_simple_contours())
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_returns_list_of_contours(self):
        projected, _ = project_contours(_simple_contours())
        assert isinstance(projected, list)

    def test_all_contours_have_projected_coordinates(self):
        projected, _ = project_contours(_simple_contours())
        for c in projected:
            assert "projected_coordinates" in c

    def test_original_coordinates_unchanged(self):
        original = _simple_contours()
        original_coords = [list(pt) for pt in original[0]["coordinates"]]
        projected, _ = project_contours(original)
        assert projected[0]["coordinates"] == original_coords

    def test_original_key_still_present(self):
        projected, _ = project_contours(_simple_contours())
        for c in projected:
            assert "coordinates" in c

    def test_projected_point_count_matches_original(self):
        projected, _ = project_contours(_simple_contours())
        for orig, proj in zip(_simple_contours(), projected):
            assert len(proj["projected_coordinates"]) == len(orig["coordinates"])

    def test_projected_values_are_numeric(self):
        projected, _ = project_contours(_simple_contours())
        for c in projected:
            for x, y in c["projected_coordinates"]:
                assert isinstance(x, float)
                assert isinstance(y, float)

    def test_crs_info_has_required_keys(self):
        _, crs_info = project_contours(_simple_contours())
        assert "epsg" in crs_info
        assert "name" in crs_info
        assert "unit" in crs_info

    def test_crs_unit_is_metre(self):
        _, crs_info = project_contours(_simple_contours())
        assert crs_info["unit"] == "metre"

    def test_crs_epsg_is_integer(self):
        _, crs_info = project_contours(_simple_contours())
        assert isinstance(crs_info["epsg"], int)

    def test_india_data_gets_zone_44n(self):
        """Specifically verify the sample dataset region → EPSG 32644."""
        _, crs_info = project_contours(_simple_contours(lon=81.29, lat=21.26))
        assert crs_info["epsg"] == 32644

    def test_other_region_gets_different_zone(self):
        """Different geographic region → different UTM zone (not hardcoded)."""
        _, crs_uk = project_contours(_simple_contours(lon=-1.0, lat=51.5))
        _, crs_india = project_contours(_simple_contours(lon=81.29, lat=21.26))
        assert crs_uk["epsg"] != crs_india["epsg"]

    def test_empty_contours_raises(self):
        with pytest.raises(ValueError, match="empty"):
            project_contours([])

    def test_other_fields_preserved(self):
        """id and elevation must survive the projection step."""
        projected, _ = project_contours(_simple_contours())
        assert projected[0]["id"] == 0
        assert projected[0]["elevation"] == 270.0

    def test_contour_count_unchanged(self):
        contours = _simple_contours()
        projected, _ = project_contours(contours)
        assert len(projected) == len(contours)
