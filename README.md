# Village Pond Planning System — Backend

A Flask-based REST API for analysing contour survey files (KML/KMZ) to assist
in planning and sizing village ponds.

---

## Project Structure

```
pond-planning/
├── app.py                      # App factory & entry point
├── routes/
│   └── contour.py              # Blueprint: POST /api/analyzeContour
├── services/
│   └── contour_service.py      # Upload pipeline (validate → save → parse → analyse → project)
├── analysis/
│   ├── terrain.py              # Contour validation & terrain metadata ✅
│   └── catchment.py            # Catchment / runoff analysis (stub)
├── utils/
│   ├── kml_parser.py           # KML/KMZ parser ✅
│   └── projection.py           # Coordinate projection (lon/lat → metres) ✅
├── tests/
│   ├── test_contour_route.py   # HTTP layer integration tests
│   ├── test_kml_parser.py      # KML/KMZ parser unit tests
│   ├── test_terrain.py         # Terrain analysis unit tests
│   └── test_projection.py      # Coordinate projection unit tests
├── verify_kml.py               # Manual verification CLI tool ✅
├── uploads/                    # Uploaded files (git-ignored)
├── requirements.txt
└── README.md
```

---

## Quick Start

```bash
# 1. Activate your virtual environment
source my_env/bin/activate

# 2. Install dependencies  (includes pyproj for coordinate projection)
pip install -r requirements.txt

# 3. Run the development server
python app.py
```

The API will be available at `http://localhost:5000`.

---

## Implemented Features

### File Upload & Validation
- Accepts `multipart/form-data` POST requests with `.kml` or `.kmz` files
- Extension validation with clear error responses (`415` for unsupported types, `422` for malformed content)
- Uploaded files are saved to the `uploads/` directory for further processing
- App factory pattern with CORS support and configurable upload folder

### KML/KMZ Parsing — `utils/kml_parser.py`
- Supports both `.kml` (parsed directly) and `.kmz` (unzipped, inner KML extracted and parsed)
- Handles KML XML namespaces automatically — works with `opengis.net/kml/2.2`, Google Earth variants, and namespace-free files
- Parses every `<Placemark>` that contains a `<LineString>`
- Extracts **elevation** from `<name>` — handles integers, decimals, and unit suffixes (e.g. `277`, `277.5`, `277m`)
- Extracts **coordinates** as `[lon, lat]` pairs; altitude is discarded
- Extracts **contour ID** from `<ExtendedData>` (`SimpleData` and `Data/value` variants), falls back to the loop index
- Placemarks without a numeric elevation or without a `<LineString>` are skipped gracefully
- Raises `KMLParseError` with a descriptive message on empty files, malformed XML, or no valid contours found

### Terrain Validation & Metadata — `analysis/terrain.py`
- Validates every contour in the dataset before computing any statistics:
  - Missing or non-numeric elevation values
  - Missing or empty coordinate lists
  - Contours with fewer than 2 points (geometrically degenerate)
  - Longitude outside `[-180, 180]` or latitude outside `[-90, 90]`
  - Fewer than 2 unique elevation levels (cannot derive a contour interval)
- Computes the following terrain metadata from the validated dataset:
  - **Contour count** and **min/max elevation**
  - **Contour interval** — derived from the sorted unique elevations; the most frequent gap is used as the representative interval, with a `contour_interval_uniform` flag indicating whether all gaps are equal
  - **Total coordinate points** across all contours
  - **Geographic bounds** — min/max longitude and latitude

### Coordinate Projection — `utils/projection.py`
- Converts geographic `[lon, lat]` (WGS 84 degrees) into projected `[X, Y]` coordinates in **metres**
- Automatically selects the appropriate **UTM zone** from the centroid of the input data — nothing is hardcoded, making it reusable for any geographic location
- Each contour dict gains a `projected_coordinates` key; the original `coordinates` key is preserved unchanged
- The chosen CRS (EPSG code, name, unit) is returned alongside the projected data and included in the API response
- Projected coordinates are used internally by the pipeline for metric calculations; only the CRS metadata is returned in the API response

---

## Processing Pipeline

```
Upload (KML/KMZ)
      ↓
[utils/kml_parser.py]  →  list of contours  {id, elevation, coordinates: [[lon, lat]]}
      ↓
[analysis/terrain.py]  →  validate + extract terrain metadata
      ↓
[utils/projection.py]  →  add projected_coordinates: [[X_m, Y_m]]  (UTM auto-selected)
      ↓
API response with terrain metadata + CRS
      ↓
[Next] DEM generation  →  regular grid in projected space
      ↓
[Next] Slope + flow direction
      ↓
[Next] Catchment area
```

---

## API Reference

### `POST /api/analyzeContour`

Accepts a KML or KMZ contour survey file, parses it, validates the terrain
data, projects coordinates, and returns a structured metadata response.

**Request** — `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `file` | file | `.kml` or `.kmz` survey file |

**Success Response** — `200 OK`

```json
{
  "status": "success",
  "filename": "contours_1m.kml",
  "terrain": {
    "contour_count": 1355,
    "min_elevation_m": 267.0,
    "max_elevation_m": 298.0,
    "contour_interval_m": 1.0,
    "contour_interval_uniform": true,
    "total_points": 159113,
    "bounds": {
      "min_lon": 81.2814044952393,
      "min_lat": 21.2398224433387,
      "max_lon": 81.3126468658447,
      "max_lat": 21.2635806472203
    },
    "crs": {
      "epsg": 32644,
      "name": "WGS 84 / UTM zone 44N",
      "unit": "metre"
    }
  }
}
```

**Error Responses**

| Status | Reason |
|--------|--------|
| `400` | Missing `file` field or empty filename |
| `415` | Unsupported file type (must be `.kml` or `.kmz`) |
| `422` | File is malformed, unparseable, or fails terrain validation |

**cURL example**

```bash
curl -X POST http://localhost:5000/api/analyzeContour \
     -F "file=@/path/to/survey.kml"
```

---

## Running Tests

```bash
python -m pytest tests/ -v
```

85 tests across 4 test modules — all passing.

| Module | Tests | Covers |
|--------|-------|--------|
| `test_contour_route.py` | 5 | HTTP layer, status codes, response shape |
| `test_kml_parser.py` | 20 | KML/KMZ parsing, namespaces, edge cases |
| `test_terrain.py` | 27 | Stats, interval logic, bounds, all validation errors |
| `test_projection.py` | 33 | UTM zone selection, coordinate projection, pipeline |

---

## Manual Verification

To test the full pipeline against your own KML file:

```bash
python verify_kml.py /path/to/your/file.kml
```

Prints terrain metadata, CRS info, a projected spot-check, and saves
the full contour list to `parsed_output.json`.

---

## Roadmap

- [x] Flask API skeleton with file upload endpoint
- [x] KML/KMZ parser (`utils/kml_parser.py`)
- [x] Terrain validation & metadata (`analysis/terrain.py`)
- [x] Coordinate projection to UTM metres (`utils/projection.py`)
- [ ] DEM generation from projected contour data
- [ ] `analysis/catchment.py` — catchment area, runoff coefficient, water yield
- [ ] Pond site suitability scoring
