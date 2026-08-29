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


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["UPLOAD_FOLDER"] = str(tmp_path)  # isolated temp dir per test
    with app.test_client() as c:
        yield c


# ── Happy-path tests ────────────────────────────────────────────────────────


def test_valid_kml_upload(client):
    """A valid .kml file returns 200 with status/filename/contour_count."""
    data = {"file": (io.BytesIO(MINIMAL_KML), "test_site.kml")}
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
    assert terrain["contour_count"] == 2
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


# ── Error-path tests ────────────────────────────────────────────────────────


def test_missing_file_field(client):
    """Request without a 'file' field should return 400."""
    response = client.post("/api/analyzeContour", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_empty_filename(client):
    """Request with an empty filename should return 400."""
    data = {"file": (io.BytesIO(b""), "")}
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_invalid_extension(client):
    """A non-KML/KMZ file should return 415."""
    data = {"file": (io.BytesIO(b"data"), "report.pdf")}
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 415
    assert response.get_json()["status"] == "error"


def test_malformed_kml_returns_422(client):
    """A .kml file with broken XML should return 422 Unprocessable Entity."""
    data = {"file": (io.BytesIO(b"<kml><broken"), "bad.kml")}
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 422
    assert response.get_json()["status"] == "error"
