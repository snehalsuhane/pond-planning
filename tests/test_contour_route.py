"""
Tests: test_contour_route

Integration tests for POST /api/analyzeContour.

Note: happy-path upload tests (valid KML/KMZ content) live in
test_kml_parser.py. These tests focus on the HTTP layer.
"""

import io
import pytest
from app import create_app


MINIMAL_KML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<kml xmlns="http://www.opengis.net/kml/2.2">'
    b"<Document>"
    b"<Placemark>"
    b"  <name>277</name>"
    b"  <LineString>"
    b"    <coordinates>81.286321,21.263539 81.286400,21.263518</coordinates>"
    b"  </LineString>"
    b"</Placemark>"
    b"<Placemark>"
    b"  <name>278</name>"
    b"  <LineString>"
    b"    <coordinates>81.286500,21.263600 81.286600,21.263700</coordinates>"
    b"  </LineString>"
    b"</Placemark>"
    b"</Document></kml>"
)


def _make_valid_kml() -> bytes:
    """Generate a KML with three dense concentric contour rings.
    The rings are large enough to produce a real DEM and valid pond candidates.
    """
    import math

    def ring(lat_c: float, lon_c: float, radius_deg: float, n: int = 48) -> str:
        # KML coordinates: space-separated "lon,lat" pairs
        pts = []
        for i in range(n + 1):
            angle = 2 * math.pi * i / n
            lat = lat_c + radius_deg * math.sin(angle)
            lon = lon_c + radius_deg * math.cos(angle)
            pts.append(f"{lon:.6f},{lat:.6f}")
        return " ".join(pts)

    lat_c, lon_c = 21.26, 81.29
    contours = [(277, 0.003), (278, 0.002), (279, 0.001)]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append('<kml xmlns="http://www.opengis.net/kml/2.2"><Document>')
    for elev, r in contours:
        parts.append(
            f"<Placemark><name>{elev}</name>"
            f"<LineString><coordinates>{ring(lat_c, lon_c, r)}</coordinates>"
            f"</LineString></Placemark>"
        )
    parts.append("</Document></kml>")
    return "\n".join(parts).encode()


VALID_KML: bytes = _make_valid_kml()


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["UPLOAD_FOLDER"] = str(tmp_path)  # isolated temp dir per test
    with app.test_client() as c:
        yield c


# ── Happy-path tests ────────────────────────────────────────────────────────


def test_valid_kml_upload(client):
    """A valid .kml file returns 200 with all expected top-level keys."""
    data = {"contour_map": (io.BytesIO(VALID_KML), "test_site.kml")}
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "success"
    assert body["filename"] == "test_site.kml"
    terrain = body["terrain"]
    assert "min_elevation_m" in terrain
    assert "max_elevation_m" in terrain
    assert "total_points" in terrain
    assert "bounds" in terrain
    assert "crs" in terrain
    # DEM metadata block
    assert "dem" in body
    dem = body["dem"]
    assert dem["resolution_m"] > 0
    assert len(dem["shape"]) == 2
    assert dem["nan_fraction"] == 0.0
    assert "slope" in dem
    # Pond candidates block
    assert "pond_candidates" in body
    candidates = body["pond_candidates"]
    assert len(candidates) > 0
    site = candidates[0]
    assert "latitude" in site and "longitude" in site
    assert "elevation_m" in site and "slope_deg" in site
    assert "score" in site and "tpi" in site
    assert "criteria" in site
    assert "elevation_score" in site["criteria"]
    # Catchment block
    assert "catchment" in site
    catchment = site["catchment"]
    assert "area_m2" in catchment
    assert "area_ha" in catchment
    assert "area_km2" in catchment
    assert "polygon" in catchment
    # pour_point is intentionally NOT in catchment (lat/lon live on the candidate itself)
    assert "pour_point" not in catchment


# ── Error-path tests ────────────────────────────────────────────────────────


def test_missing_file_field(client):
    """Request without a 'contour_map' field should return 400."""
    response = client.post("/api/analyzeContour", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_empty_filename(client):
    """Request with an empty filename should return 400."""
    data = {"contour_map": (io.BytesIO(b""), "")}
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_invalid_extension(client):
    """A non-KML/KMZ file should return 415."""
    data = {"contour_map": (io.BytesIO(b"data"), "report.pdf")}
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 415
    assert response.get_json()["status"] == "error"


def test_malformed_kml_returns_422(client):
    """A .kml file with broken XML should return 422 Unprocessable Entity."""
    data = {"contour_map": (io.BytesIO(b"<kml><broken"), "bad.kml")}
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 422
    assert response.get_json()["status"] == "error"
