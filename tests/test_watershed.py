import pytest
import numpy as np
from analysis.watershed import delineate_watershed

@pytest.fixture
def dem_result_mock():
    return {
        "x_coords": np.arange(5) * 10.0,
        "y_coords": np.arange(5) * 10.0,
        "resolution_m": 10.0
    }

class TestDelineateWatershed:
    def test_straight_line_flow(self, dem_result_mock):
        """Test a simple straight line flow where cells point West."""
        fdir = np.zeros((5, 5), dtype=np.int16)
        fdir[2, 1] = 16  # points to 2,0
        fdir[2, 2] = 16  # points to 2,1
        fdir[2, 3] = 16  # points to 2,2
        fdir[2, 4] = 16  # points to 2,3
        
        result = delineate_watershed({"flow_direction": fdir}, (2, 1), dem_result_mock, 32644)
        
        # 4 cells * 10^2 = 400 m2
        assert result["area_m2"] == 400.0
        assert len(result["polygon_lonlat"]) > 0

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
        
        result = delineate_watershed({"flow_direction": fdir}, (4, 2), dem_result_mock, 32644)
        
        # Pour point + (3,1) + (3,3) + (2,0) = 4 cells = 400m2
        assert result["area_m2"] == 400.0
        
        # Each coordinate in the polygon should be a 2-element list [lon, lat]
        for coord in result["polygon_lonlat"]:
            assert isinstance(coord, list)
            assert len(coord) == 2
            assert isinstance(coord[0], float)
            assert isinstance(coord[1], float)

    def test_no_upstream_flow(self, dem_result_mock):
        """Test a pour point at the top of a ridge with no upstream cells."""
        fdir = np.zeros((5, 5), dtype=np.int16)
        fdir[2, 2] = 16 # Pour point flows West, but nothing flows into it
        
        result = delineate_watershed({"flow_direction": fdir}, (2, 2), dem_result_mock, 32644)
        
        # Area should just be the pour point itself (1 cell = 100m2)
        assert result["area_m2"] == 100.0
        assert len(result["polygon_lonlat"]) > 0

    def test_out_of_bounds_pour_point(self, dem_result_mock):
        fdir = np.zeros((5, 5), dtype=np.int16)
        with pytest.raises(ValueError, match="out of bounds"):
            delineate_watershed({"flow_direction": fdir}, (10, 10), dem_result_mock, 32644)
