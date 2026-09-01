import pytest
import numpy as np
from analysis.catchment import delineate_catchment

@pytest.fixture
def dem_result_mock():
    return {
        "x_coords": np.arange(5) * 10.0,
        "y_coords": np.arange(5) * 10.0,
        "resolution_m": 10.0
    }

class TestDelineateCatchment:
    def test_straight_line_flow(self, dem_result_mock):
        """Test a simple straight line flow where cells point West."""
        fdir = np.zeros((5, 5), dtype=np.int16)
        fdir[2, 1] = 16  # points to 2,0
        fdir[2, 2] = 16  # points to 2,1
        fdir[2, 3] = 16  # points to 2,2
        fdir[2, 4] = 16  # points to 2,3
        
        result = delineate_catchment({"flow_direction": fdir}, (2, 1), dem_result_mock, 32644)
        
        # 4 cells * 10^2 = 400 m2
        assert result["area_m2"] == 400.0
        assert result["area_ha"] == 0.04
        assert result["area_km2"] == 0.0004
        assert len(result["polygon"]) > 0

    def test_diagonal_converging_flow(self, dem_result_mock):
        """Test a V-shaped valley flowing into a single pour point."""
        fdir = np.zeros((5, 5), dtype=np.int16)
        # Pour point at (4, 2). 
        # (3, 1) flows SE (code 2) to (4,2)
        fdir[3, 1] = 2
        # (3, 3) flows SW (code 8) to (4,2)
        fdir[3, 3] = 8
        # (2, 0) flows SE to (3,1)
        fdir[2, 0] = 2
        
        result = delineate_catchment({"flow_direction": fdir}, (4, 2), dem_result_mock, 32644)
        
        # Pour point + (3,1) + (3,3) + (2,0) = 4 cells = 400m2
        assert result["area_m2"] == 400.0
        assert result["area_ha"] == 0.04
        assert result["area_km2"] == 0.0004
        
        # Each coordinate in the polygon should be a 2-element list [lon, lat]
        for coord in result["polygon"]:
            assert isinstance(coord, list)
            assert len(coord) == 2
            assert isinstance(coord[0], float)
            assert isinstance(coord[1], float)

    def test_no_upstream_flow(self, dem_result_mock):
        """Test a pour point at the top of a ridge with no upstream cells."""
        fdir = np.zeros((5, 5), dtype=np.int16)
        fdir[2, 2] = 16 # Pour point flows West, but nothing flows into it
        
        result = delineate_catchment({"flow_direction": fdir}, (2, 2), dem_result_mock, 32644)
        
        # Area should just be the pour point itself (1 cell = 100m2)
        assert result["area_m2"] == 100.0
        assert result["area_ha"] == 0.01
        assert result["area_km2"] == 0.0001
        assert len(result["polygon"]) > 0

    def test_out_of_bounds_pour_point(self, dem_result_mock):
        fdir = np.zeros((5, 5), dtype=np.int16)
        with pytest.raises(ValueError, match="out of bounds"):
            delineate_catchment({"flow_direction": fdir}, (10, 10), dem_result_mock, 32644)

    def test_area_unit_consistency(self, dem_result_mock):
        """area_ha must equal area_m2 / 10000 and area_km2 must equal area_m2 / 1e6."""
        fdir = np.zeros((5, 5), dtype=np.int16)
        fdir[2, 1] = 16
        fdir[2, 2] = 16
        fdir[2, 3] = 16
        result = delineate_catchment({"flow_direction": fdir}, (2, 1), dem_result_mock, 32644)

        assert pytest.approx(result["area_ha"],  rel=1e-9) == result["area_m2"] / 10_000
        assert pytest.approx(result["area_km2"], rel=1e-9) == result["area_m2"] / 1_000_000

    def test_polygon_coords_are_valid_wgs84(self, dem_result_mock):
        """All polygon coordinates must be valid WGS84 lon/lat values."""
        fdir = np.zeros((5, 5), dtype=np.int16)
        fdir[2, 1] = 16
        fdir[2, 2] = 16
        fdir[2, 3] = 16
        result = delineate_catchment({"flow_direction": fdir}, (2, 1), dem_result_mock, 32644)

        for lon, lat in result["polygon"]:
            assert -180.0 <= lon <= 180.0, f"Longitude {lon} out of WGS84 range"
            assert  -90.0 <= lat <=  90.0, f"Latitude {lat} out of WGS84 range"

    def test_empty_polygon_when_isolated_cell(self):
        """A single isolated pour point on a tiny grid may produce an empty polygon — must not crash."""
        mock = {"x_coords": np.array([0.0, 10.0]), "y_coords": np.array([0.0, 10.0]), "resolution_m": 10.0}
        fdir = np.zeros((2, 2), dtype=np.int16)
        result = delineate_catchment({"flow_direction": fdir}, (0, 0), mock, 32644)
        assert "polygon" in result
        assert "area_m2" in result
