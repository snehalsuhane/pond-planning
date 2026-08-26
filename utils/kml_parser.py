"""
Utility: kml_parser

Parses KML and KMZ files and extracts geographic primitives
(coordinates, polygons, placemarks).

Status: STUB — implementation will follow once file-saving is wired up.
"""


def parse(filepath: str) -> dict:
    """
    Parse a KML or KMZ file and return a structured geodata dict.

    Parameters
    ----------
    filepath : absolute path to the saved KML / KMZ file

    Returns
    -------
    dict with keys:
        - coordinates : list of (lon, lat, alt) tuples
        - polygons    : list of polygon rings
        - placemarks  : list of placemark metadata dicts
    """
    raise NotImplementedError("kml_parser.parse() is not yet implemented.")
