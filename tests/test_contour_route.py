"""
Tests: test_contour_route

Integration tests for POST /api/analyzeContour.

Note: happy-path upload tests (valid KML/KMZ content) live in
test_kml_parser.py. These tests focus on the HTTP layer.
"""

import io
import json
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
    contours = [(277, 0.003), (279, 0.002), (277, 0.001)]
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


def test_land_selection_preserves_full_survey_catchments(client):
    from shapely.geometry import box, mapping, Point
    selection = box(81.286, 21.256, 81.294, 21.264)
    baseline = client.post('/api/analyzeContour', data={
        'contour_map': (io.BytesIO(VALID_KML), 'baseline.kml')}).get_json()
    response = client.post('/api/analyzeContour', data={
        'contour_map': (io.BytesIO(VALID_KML), 'selected.kml'),
        'land_area': json.dumps(mapping(selection))})
    assert response.status_code == 200
    body = response.get_json()
    assert body['pond_candidates'] == baseline['pond_candidates']
    assert body['land_selection']['partial_terrain_coverage']
    assert body['planning']['site_scope'] == 'selected_land'
    assert body['planning']['terrain_scope'] == 'full_uploaded_survey'
    assert body['planning']['catchments_clipped_to_land'] is False
    assert all(selection.covers(Point(s['longitude'], s['latitude'])) for s in body['pond_candidates'])


def test_selection_outside_survey_fails_before_water_lookup(client, monkeypatch):
    from shapely.geometry import box, mapping
    def unexpected_lookup(*args, **kwargs):
        pytest.fail('Outside-survey land should fail before external water screening')
    monkeypatch.setattr('services.contour_service.screen_waterways', unexpected_lookup)
    response = client.post('/api/analyzeContour', data={
        'contour_map': (io.BytesIO(VALID_KML), 'outside.kml'),
        'land_area': json.dumps(mapping(box(80, 20, 80.001, 20.001)))})
    assert response.status_code == 422
    assert 'does not overlap' in response.get_json()['error']


@pytest.mark.parametrize('land', ['not json', 'null', '{}', ''])
def test_invalid_land_boundary_returns_400(client, land):
    response = client.post('/api/analyzeContour', data={
        'contour_map': (io.BytesIO(VALID_KML), 'invalid.kml'), 'land_area': land})
    assert response.status_code == 400
    assert 'land_area' in response.get_json()['error']


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests.test_water_volume import fake_rainfall
    monkeypatch.setattr('services.water_volume.fetch_rainfall', fake_rainfall)
    monkeypatch.setattr("services.waterways.fetch_waterways", lambda bounds: {"elements": []})
    app = create_app()
    app.config["TESTING"] = True
    app.config["POND_EDGE_SETBACK_M"] = 10.0
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
    assert 0 < len(candidates) <= 5
    site = candidates[0]
    assert "latitude" in site and "longitude" in site
    assert "elevation_m" in site and "slope_deg" in site
    assert "score" in site and "catchment" in site
    assert "criteria" in site
    assert "slope_score" in site["criteria"]
    # Catchment block
    assert "catchment" in site
    catchment = site["catchment"]
    assert "area_m2" in catchment
    assert "area_ha" in catchment
    assert "area_km2" in catchment
    assert "polygon" in catchment
    assert "footprint" not in site
    assert site["collection_type"] in {"natural_depression", "drainage_outlet"}
    assert "collection_point" in catchment
    assert "geometry" in catchment
    assert site["distance_to_domain_edge_m"] >= 10
    assert body["waterway_screening"]["status"] == "screened_against_mapped_water"
    assert body["planning"]["catchment_display_unit"] == "ha"
    assert isinstance(catchment["boundary_truncated"], bool)
    assert "drainage_score" in site["criteria"]


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


def test_waterway_lookup_failure_returns_no_recommendations(client, monkeypatch):
    from services.waterways import WaterwayDataError
    def fail(bounds):
        raise WaterwayDataError('Waterway lookup failed')
    monkeypatch.setattr('services.waterways.fetch_waterways', fail)
    response = client.post('/api/analyzeContour', data={'contour_map': (io.BytesIO(VALID_KML), 'site.kml')},
                           content_type='multipart/form-data')
    assert response.status_code == 503
    assert response.get_json()['waterway_screening']['status'] == 'unavailable'
    assert 'pond_candidates' not in response.get_json()


def test_kmz_upload_uses_same_route(client):
    import zipfile
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        archive.writestr('doc.kml', VALID_KML)
    content.seek(0)
    response = client.post('/api/analyzeContour',
                           data={'contour_map': (content, 'site.kmz')},
                           content_type='multipart/form-data')
    assert response.status_code == 200
    body = response.get_json()
    assert body['planning']['ranking_version'] == 'collection_targets_v3'
    assert len(body['pond_candidates']) <= 5
    assert body['pond_candidates'][0]['catchment']['area_ha'] > 0
    assert 'footprint' not in body['pond_candidates'][0]


@pytest.mark.parametrize('coefficient', ['-1', '2', 'nan', 'abc'])
def test_contour_invalid_runoff(client, coefficient):
    response = client.post('/api/analyzeContour', data={'contour_map': (io.BytesIO(VALID_KML), 'test.kml'),
                                                     'runoff_coefficient': coefficient})
    assert response.status_code == 400


def test_contour_volume_uses_selected_coefficient(client):
    data = client.post('/api/analyzeContour', data={'contour_map': (io.BytesIO(VALID_KML), 'test.kml'),
                                                  'runoff_coefficient': '0.5'}).get_json()
    assert data['pond_candidates']
    for site in data['pond_candidates']:
        assert site['water_volume']['annual_m3'] == pytest.approx(site['catchment']['area_m2'] * .5)
