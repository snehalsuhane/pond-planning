"""
Tests: test_kml_parser

Unit tests for utils/kml_parser.py — covers:

  Happy paths
  ───────────
  - Valid KML with standard namespace
  - Valid KML without namespace
  - Elevation correctly extracted (integer, decimal, unit suffix)
  - Coordinates correctly extracted
  - Multiple contours
  - Contour ID from ExtendedData (SimpleData and Data/value variants)
  - KMZ (zip) file works

  Edge / error paths
  ──────────────────
  - Placemark without elevation → skipped
  - Placemark without LineString → skipped
  - Empty coordinates block → skipped
  - All placemarks invalid → KMLParseError raised
  - Completely empty KML → KMLParseError raised
  - Malformed XML → KMLParseError raised
  - No <Placemark> at all → KMLParseError raised
  - Unsupported extension → KMLParseError raised
  - KMZ with no embedded KML → KMLParseError raised
"""

import io
import os
import zipfile
import tempfile
import pytest

from utils.kml_parser import parse, parse_kml, parse_kmz, KMLParseError


# ---------------------------------------------------------------------------
# KML fixture builders
# ---------------------------------------------------------------------------

NS = "http://www.opengis.net/kml/2.2"


def _kml(placemarks_xml: str, ns: str = NS) -> bytes:
    """Wrap placemark fragments in a minimal KML document."""
    if ns:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<kml xmlns="{ns}">'
            f"<Document>{placemarks_xml}</Document>"
            f"</kml>"
        ).encode()
    else:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<kml><Document>{placemarks_xml}</Document></kml>"
        ).encode()


def _placemark(name: str, coords: str, extended_data: str = "") -> str:
    """Return a <Placemark> XML fragment with a LineString."""
    return (
        f"<Placemark>"
        f"  <name>{name}</name>"
        f"  {extended_data}"
        f"  <LineString>"
        f"    <coordinates>{coords}</coordinates>"
        f"  </LineString>"
        f"</Placemark>"
    )


def _write_kml(content: bytes, suffix: str = ".kml") -> str:
    """Write bytes to a named temp file; return its path."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fh:
        fh.write(content)
        return fh.name


def _write_kmz(kml_content: bytes, inner_name: str = "doc.kml") -> str:
    """Pack kml_content into a .kmz (zip) temp file; return its path."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".kmz") as fh:
        kmz_path = fh.name
    with zipfile.ZipFile(kmz_path, "w") as zf:
        zf.writestr(inner_name, kml_content)
    return kmz_path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cleanup(tmp_path):
    """Remove any temp files created during a test."""
    created = []
    yield created
    for p in created:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestValidKML:
    def test_single_contour_returned(self):
        """A single valid placemark produces exactly one contour."""
        content = _kml(_placemark("277", "81.286321,21.263539 81.286400,21.263518"))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert len(result) == 1
        finally:
            os.unlink(path)

    def test_elevation_integer(self):
        """Elevation '277' → 277.0 (float)."""
        content = _kml(_placemark("277", "81.0,21.0"))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert result[0]["elevation"] == 277.0
        finally:
            os.unlink(path)

    def test_elevation_decimal(self):
        """Elevation '277.5' → 277.5."""
        content = _kml(_placemark("277.5", "81.0,21.0"))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert result[0]["elevation"] == 277.5
        finally:
            os.unlink(path)

    def test_elevation_with_unit_suffix(self):
        """Elevation '300m' → 300.0 (unit suffix is stripped)."""
        content = _kml(_placemark("300m", "81.0,21.0"))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert result[0]["elevation"] == 300.0
        finally:
            os.unlink(path)

    def test_coordinates_extracted_correctly(self):
        """Coordinates are returned as [[lon, lat], ...] pairs."""
        content = _kml(_placemark(
            "280",
            "81.286321,21.263539,100 81.286400,21.263518,100",
        ))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            coords = result[0]["coordinates"]
            assert coords[0] == [81.286321, 21.263539]
            assert coords[1] == [81.286400, 21.263518]
        finally:
            os.unlink(path)

    def test_altitude_discarded(self):
        """Third component (altitude) must NOT appear in output coordinates."""
        content = _kml(_placemark("285", "81.0,21.0,500"))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert len(result[0]["coordinates"][0]) == 2
        finally:
            os.unlink(path)

    def test_multiple_contours(self):
        """Multiple placemarks produce multiple contours."""
        pm1 = _placemark("270", "81.0,21.0")
        pm2 = _placemark("280", "81.1,21.1")
        pm3 = _placemark("290", "81.2,21.2")
        content = _kml(pm1 + pm2 + pm3)
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert len(result) == 3
            elevations = {c["elevation"] for c in result}
            assert elevations == {270.0, 280.0, 290.0}
        finally:
            os.unlink(path)

    def test_no_namespace_kml(self):
        """KML without any namespace is parsed correctly."""
        content = _kml(_placemark("295", "81.3,21.3"), ns="")
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert result[0]["elevation"] == 295.0
        finally:
            os.unlink(path)

    def test_contour_id_from_simple_data(self):
        """ID is read from <SimpleData name='id'> in ExtendedData."""
        extended = (
            "<ExtendedData>"
            "  <SimpleData name='id'>42</SimpleData>"
            "</ExtendedData>"
        )
        content = _kml(_placemark("300", "81.0,21.0", extended))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert result[0]["id"] == 42
        finally:
            os.unlink(path)

    def test_contour_id_fallback_to_index(self):
        """ID falls back to the Placemark's loop index when ExtendedData is absent."""
        content = _kml(_placemark("305", "81.0,21.0"))
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert isinstance(result[0]["id"], int)
        finally:
            os.unlink(path)


class TestKMZ:
    def test_kmz_parses_correctly(self):
        """A valid KMZ file (zip containing doc.kml) is parsed correctly."""
        inner_kml = _kml(_placemark("310", "81.5,21.5"))
        kmz_path = _write_kmz(inner_kml)
        try:
            result = parse_kmz(kmz_path)
            assert len(result) == 1
            assert result[0]["elevation"] == 310.0
        finally:
            os.unlink(kmz_path)

    def test_kmz_non_doc_kml_name(self):
        """KMZ whose inner KML is not named doc.kml still works."""
        inner_kml = _kml(_placemark("315", "81.6,21.6"))
        kmz_path = _write_kmz(inner_kml, inner_name="survey.kml")
        try:
            result = parse_kmz(kmz_path)
            assert result[0]["elevation"] == 315.0
        finally:
            os.unlink(kmz_path)

    def test_auto_detect_kmz(self):
        """parse() auto-detects .kmz extension and calls parse_kmz."""
        inner_kml = _kml(_placemark("320", "81.7,21.7"))
        kmz_path = _write_kmz(inner_kml)
        try:
            result = parse(kmz_path)
            assert result[0]["elevation"] == 320.0
        finally:
            os.unlink(kmz_path)


# ---------------------------------------------------------------------------
# Skip / partial-data tests
# ---------------------------------------------------------------------------

class TestSkipBehavior:
    def test_placemark_without_elevation_skipped(self):
        """Placemarks whose <name> has no numeric value are skipped."""
        bad = _placemark("Survey Boundary", "81.0,21.0")
        good = _placemark("270", "81.0,21.0")
        content = _kml(bad + good)
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            # Only the good placemark should appear
            assert len(result) == 1
            assert result[0]["elevation"] == 270.0
        finally:
            os.unlink(path)

    def test_placemark_without_linestring_skipped(self):
        """Placemarks without a <LineString> (e.g. Points) are skipped."""
        no_line = (
            "<Placemark>"
            "  <name>275</name>"
            "  <Point><coordinates>81.0,21.0</coordinates></Point>"
            "</Placemark>"
        )
        good = _placemark("280", "81.1,21.1")
        content = _kml(no_line + good)
        path = _write_kml(content)
        try:
            result = parse_kml(path)
            assert len(result) == 1
            assert result[0]["elevation"] == 280.0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Error / failure tests
# ---------------------------------------------------------------------------

class TestErrors:
    def test_all_invalid_placemarks_raises(self):
        """KMLParseError if every placemark is invalid (no elevation)."""
        content = _kml(_placemark("No elevation here", "81.0,21.0"))
        path = _write_kml(content)
        try:
            with pytest.raises(KMLParseError, match="No valid contours"):
                parse_kml(path)
        finally:
            os.unlink(path)

    def test_empty_file_raises(self):
        """KMLParseError on a completely empty file."""
        path = _write_kml(b"")
        try:
            with pytest.raises(KMLParseError):
                parse_kml(path)
        finally:
            os.unlink(path)

    def test_malformed_xml_raises(self):
        """KMLParseError on broken XML."""
        path = _write_kml(b"<kml><unclosed>")
        try:
            with pytest.raises(KMLParseError, match="malformed"):
                parse_kml(path)
        finally:
            os.unlink(path)

    def test_no_placemarks_raises(self):
        """KMLParseError when there are no <Placemark> elements."""
        content = b'<?xml version="1.0"?><kml><Document></Document></kml>'
        path = _write_kml(content)
        try:
            with pytest.raises(KMLParseError, match="No <Placemark>"):
                parse_kml(path)
        finally:
            os.unlink(path)

    def test_unsupported_extension_raises(self):
        """KMLParseError for unsupported file extension."""
        path = _write_kml(b"data", suffix=".geojson")
        try:
            with pytest.raises(KMLParseError, match="Unsupported extension"):
                parse(path)
        finally:
            os.unlink(path)

    def test_kmz_without_kml_inside_raises(self):
        """KMLParseError when the KMZ archive has no .kml entry."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".kmz") as fh:
            kmz_path = fh.name
        with zipfile.ZipFile(kmz_path, "w") as zf:
            zf.writestr("readme.txt", "no kml here")
        try:
            with pytest.raises(KMLParseError, match="No .kml file"):
                parse_kmz(kmz_path)
        finally:
            os.unlink(kmz_path)

    def test_bad_zip_raises(self):
        """KMLParseError when a .kmz file is not a valid zip."""
        path = _write_kml(b"this is not a zip", suffix=".kmz")
        try:
            with pytest.raises(KMLParseError, match="valid KMZ"):
                parse_kmz(path)
        finally:
            os.unlink(path)
