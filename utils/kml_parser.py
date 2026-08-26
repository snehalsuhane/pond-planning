"""
Utility: kml_parser

Parses KML and KMZ files into structured contour data.

Supports:
  - .kml  — parsed directly
  - .kmz  — unzipped first; primary KML (doc.kml or first *.kml) is read

Each valid <Placemark> containing a <LineString> is converted to:
    {
        "id":          int,
        "elevation":   float,
        "coordinates": [[lon, lat], ...]
    }

Placemarks without a numeric elevation or without a <LineString> are skipped.
A KMLParseError is raised if the file is malformed or no valid contours exist.
"""

import re
import zipfile
from typing import Optional
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class KMLParseError(ValueError):
    """Raised when the KML/KMZ file cannot be parsed into valid contours."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_namespace(root: ET.Element) -> str:
    """
    Extract the XML namespace URI from the root element tag.
    Returns an empty string when no namespace is present.
    """
    tag = root.tag
    if tag.startswith("{"):
        return tag[1: tag.index("}")]
    return ""


def _ns(tag: str, ns: str) -> str:
    """Prefix *tag* with the namespace URI (or return bare tag if ns is empty)."""
    return f"{{{ns}}}{tag}" if ns else tag


def _find(element: ET.Element, tag: str, ns: str) -> Optional[ET.Element]:
    """Find a direct or nested child by tag, respecting the KML namespace."""
    return element.find(_ns(tag, ns))


def _findall_deep(element: ET.Element, tag: str, ns: str) -> list:
    """Find *all* descendants matching tag, respecting namespace."""
    return element.findall(f".//{_ns(tag, ns)}")


def _parse_elevation(name: str) -> Optional[float]:
    """
    Extract a numeric elevation from a Placemark <name>.

    Handles:
      "277"       → 277.0
      "277.5"     → 277.5
      "277 m"     → 277.0
      "277m asl"  → 277.0
      "Site A"    → None
    """
    if not name:
        return None
    match = re.match(r"^\s*([-+]?\d+(?:\.\d+)?)", name.strip())
    return float(match.group(1)) if match else None


def _parse_coordinates(coord_text: str) -> list:
    """
    Convert a KML coordinate string into [[lon, lat], ...] pairs.

    KML format: "lon,lat[,alt]" tuples separated by whitespace.
    Altitude is discarded. Malformed tokens are silently skipped.
    """
    coords = []
    for token in coord_text.strip().split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                coords.append([lon, lat])
            except ValueError:
                continue
    return coords


def _extract_contour_id(placemark: ET.Element, ns: str, fallback: int) -> int:
    """
    Look for a contour/feature ID inside <ExtendedData>.

    Checks (in order):
      1. <SimpleData name="id|contour_id|fid">value</SimpleData>
      2. <Data name="id|contour_id|fid"><value>value</value></Data>

    Falls back to *fallback* (loop index) if nothing is found.
    """
    id_names = {"id", "contour_id", "fid"}

    extended = _find(placemark, "ExtendedData", ns)
    if extended is None:
        return fallback

    # Strategy 1 — SimpleData
    for el in extended.iter():
        if el.tag.endswith("SimpleData") and el.get("name", "").lower() in id_names:
            try:
                return int(el.text)
            except (TypeError, ValueError):
                pass

    # Strategy 2 — Data / value
    for el in extended.iter():
        if el.tag.endswith("Data") and el.get("name", "").lower() in id_names:
            for child in el:
                if child.tag.endswith("value"):
                    try:
                        return int(child.text)
                    except (TypeError, ValueError):
                        pass

    return fallback


# ---------------------------------------------------------------------------
# Core parsing logic
# ---------------------------------------------------------------------------

def _parse_kml_tree(root: ET.Element) -> list:
    """
    Walk an ElementTree root and convert every valid LineString Placemark
    into a contour dict.

    Skips Placemarks that:
      - have no numeric elevation in <name>
      - contain no <LineString>
      - have an empty or unparseable <coordinates> block

    Raises KMLParseError if no valid contours are extracted.
    """
    ns = _detect_namespace(root)
    placemarks = _findall_deep(root, "Placemark", ns)

    if not placemarks:
        raise KMLParseError("No <Placemark> elements found in the KML file.")

    contours = []
    skipped = 0

    for idx, placemark in enumerate(placemarks):

        # ── 1. Elevation ─────────────────────────────────────────────────────
        name_el = _find(placemark, "name", ns)
        name_text = (name_el.text or "").strip() if name_el is not None else ""
        elevation = _parse_elevation(name_text)

        if elevation is None:
            skipped += 1
            continue

        # ── 2. LineString → coordinates ──────────────────────────────────────
        # Search direct child first, then any nested LineString (MultiGeometry).
        line_string = _find(placemark, "LineString", ns)
        if line_string is None:
            candidates = _findall_deep(placemark, "LineString", ns)
            line_string = candidates[0] if candidates else None

        if line_string is None:
            skipped += 1
            continue

        coord_el = _find(line_string, "coordinates", ns)
        if coord_el is None or not coord_el.text:
            skipped += 1
            continue

        coordinates = _parse_coordinates(coord_el.text)
        if not coordinates:
            skipped += 1
            continue

        # ── 3. ID ────────────────────────────────────────────────────────────
        contour_id = _extract_contour_id(placemark, ns, idx)

        contours.append({
            "id": contour_id,
            "elevation": elevation,
            "coordinates": coordinates,
        })

    if not contours:
        raise KMLParseError(
            f"No valid contours extracted. "
            f"{len(placemarks)} placemark(s) found, "
            f"{skipped} skipped (missing elevation or LineString coordinates)."
        )

    return contours


def _parse_bytes(kml_bytes: bytes) -> list:
    """Parse raw KML bytes into a list of contour dicts."""
    if not kml_bytes.strip():
        raise KMLParseError("The KML file is empty.")
    try:
        root = ET.fromstring(kml_bytes)
    except ET.ParseError as exc:
        raise KMLParseError(f"KML XML is malformed: {exc}") from exc
    return _parse_kml_tree(root)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_kml(filepath: str) -> list:
    """Read and parse a .kml file from disk."""
    try:
        with open(filepath, "rb") as fh:
            return _parse_bytes(fh.read())
    except OSError as exc:
        raise KMLParseError(f"Cannot read '{filepath}': {exc}") from exc


def parse_kmz(filepath: str) -> list:
    """
    Unzip a .kmz file and parse its primary KML document.

    Preference order for the KML entry:
      1. doc.kml  (Google Earth convention)
      2. First *.kml entry found in the archive
    """
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise KMLParseError("No .kml file found inside the KMZ archive.")
            primary = next(
                (n for n in kml_names if n.lower() == "doc.kml"),
                kml_names[0],
            )
            kml_bytes = zf.read(primary)
    except zipfile.BadZipFile as exc:
        raise KMLParseError(f"Not a valid KMZ (zip) archive: {exc}") from exc

    return _parse_bytes(kml_bytes)


def parse(filepath: str) -> list:
    """
    Auto-detect format by extension and parse into contour dicts.

    Parameters
    ----------
    filepath : str
        Absolute (or relative) path to a .kml or .kmz file.

    Returns
    -------
    list[dict]  — each dict has keys: id, elevation, coordinates

    Raises
    ------
    KMLParseError
        If the file cannot be read, is malformed, has an unsupported extension,
        or contains no valid contour placemarks.
    """
    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
    if ext == "kml":
        return parse_kml(filepath)
    elif ext == "kmz":
        return parse_kmz(filepath)
    else:
        raise KMLParseError(
            f"Unsupported extension '.{ext}'. Expected .kml or .kmz."
        )
