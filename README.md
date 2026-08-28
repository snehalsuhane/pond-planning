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
│   └── contour_service.py      # Upload pipeline (validate → save → parse → analyse)
├── analysis/
│   ├── terrain.py              # Contour validation & terrain metadata ✅
│   └── catchment.py            # Catchment / runoff analysis (stub)
├── utils/
│   └── kml_parser.py           # KML/KMZ parser ✅
├── tests/
│   ├── test_contour_route.py   # HTTP layer integration tests
│   ├── test_kml_parser.py      # KML/KMZ parser unit tests
│   └── test_terrain.py         # Terrain analysis unit tests
├── verify_kml.py               # Manual verification CLI tool ✅
├── uploads/                    # Uploaded files (git-ignored)
├── requirements.txt
└── README.md
```

---

## Quick Start

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the development server
python app.py
```

The API will be available at `http://localhost:5000`.

---

## Implemented Features

### ✅ Flask API Skeleton (commit 1)
- App factory pattern in `app.py` with CORS and configurable upload folder
- Blueprint-based routing (`routes/contour.py`)
- `POST /api/analyzeContour` endpoint accepting `multipart/form-data`
- Extension validation — only `.kml` and `.kmz` accepted (returns `415` otherwise)
- Clean service/route separation for testability

### ✅ KML/KMZ Parser — `utils/kml_parser.py` (commit 2)
- Supports both `.kml` (direct parse) and `.kmz` (unzip → read inner KML)
- Handles KML XML namespaces automatically (`opengis.net/kml/2.2`, Google Earth variants, and no-namespace)
- Parses every `<Placemark>` containing a `<LineString>`
- Extracts **elevation** from `<name>` — handles integers, decimals, and unit suffixes (e.g. `277m`)
- Extracts **coordinates** as `[lon, lat]` pairs — altitude component is discarded
- Extracts **contour ID** from `<ExtendedData>` (`SimpleData` and `Data/value` variants), falls back to index
- Placemarks without a numeric elevation or without a `<LineString>` are skipped gracefully
- Raises `KMLParseError` with a descriptive message on empty files, malformed XML, or no valid contours

### ✅ Terrain Validation & Metadata — `analysis/terrain.py` (commit 3)
- Validates every contour in the dataset before computing any statistics:
  - Missing or non-numeric elevation → error
  - Missing or empty coordinates → error
  - Fewer than 2 coordinate points → error
  - Longitude outside `[-180, 180]` → error
  - Latitude outside `[-90, 90]` → error
  - Fewer than 2 unique elevation levels → error (cannot derive interval)
- Computes terrain metadata:
  - **Contour count**
  - **Min / max elevation** (metres)
  - **Contour interval** — derived from unique sorted elevations; the dominant (most frequent) gap is reported
  - **Uniformity flag** — `true` only when every elevation gap equals the dominant interval
  - **Total coordinate points** across all contours
  - **Geographic bounds** — min/max longitude and latitude

### ✅ Manual Verification Tool — `verify_kml.py`
- CLI tool for verifying any `.kml` or `.kmz` file against the full pipeline
- Prints a rich terminal summary: contour count, elevation range, interval, bounds, first 5 contours
- Saves the full parsed contour list to `parsed_output.json` for side-by-side comparison with the raw KML

---

## API Reference

### `POST /api/analyzeContour`

Accepts a KML or KMZ contour survey file, parses it, validates the terrain
data, and returns a structured metadata response.

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

52 tests across 3 test modules — all passing.

| Module | Tests | Covers |
|--------|-------|--------|
| `test_contour_route.py` | 5 | HTTP layer, status codes, response shape |
| `test_kml_parser.py` | 20 | KML/KMZ parsing, namespaces, edge cases |
| `test_terrain.py` | 27 | Stats, interval logic, bounds, all validation errors |

---

## Manual Verification

To test the parser against your own KML file:

```bash
python verify_kml.py /path/to/your/file.kml
```

This prints the terrain metadata summary and saves the full parsed contour
list to `parsed_output.json` for manual cross-checking against the raw KML.

---

## Roadmap

- [x] Flask API skeleton with file upload endpoint
- [x] KML/KMZ parser (`utils/kml_parser.py`)
- [x] Terrain validation & metadata (`analysis/terrain.py`)
- [ ] DEM generation from contour data
- [ ] `analysis/catchment.py` — catchment area, runoff coefficient, water yield
- [ ] Pond site suitability scoring
- [ ] Return full analysis report from `/api/analyzeContour`
