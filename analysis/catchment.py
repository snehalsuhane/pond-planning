"""
Analysis: catchment

Estimates catchment area and runoff potential.

Status: STUB — implementation will follow after terrain analysis is complete.
"""


def analyse(geodata: dict) -> dict:
    """
    Calculate catchment area and runoff estimates from geodata.

    Expected inputs (from kml_parser.parse / terrain.analyse):
        geodata['coordinates']    — list of (lon, lat, alt) tuples
        geodata['polygons']       — delineated watershed polygons
        geodata['slope_map']      — from terrain.analyse() (optional)

    Expected outputs:
        - catchment_area_m2  : total catchment area in square metres
        - runoff_coefficient : dimensionless runoff coefficient (0–1)
        - estimated_yield_m3 : estimated annual water yield in cubic metres

    Returns
    -------
    dict with catchment analysis results
    """
    raise NotImplementedError("catchment.analyse() is not yet implemented.")
