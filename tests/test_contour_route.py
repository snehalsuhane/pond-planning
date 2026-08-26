"""
Tests: test_contour_route

Integration tests for POST /api/analyzeContour.
"""

import io
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ── Happy-path tests ────────────────────────────────────────────────────────


def test_valid_kml_upload(client):
    """A valid .kml file should return 200 with the filename."""
    data = {
        "file": (io.BytesIO(b"<kml></kml>"), "test_site.kml"),
    }
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["filename"] == "test_site.kml"


def test_valid_kmz_upload(client):
    """A valid .kmz file should return 200 with the filename."""
    data = {
        "file": (io.BytesIO(b"PK\x03\x04"), "survey.kmz"),
    }
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["filename"] == "survey.kmz"


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
    data = {
        "file": (io.BytesIO(b"data"), "report.pdf"),
    }
    response = client.post(
        "/api/analyzeContour",
        data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 415
    assert response.get_json()["success"] is False
