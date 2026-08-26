# Village Pond Planning System — Backend

A Flask-based REST API for analysing contour survey files (KML/KMZ) to assist
in planning and sizing village ponds.

---

## Project Structure

```
pond-planning/
├── app.py                    # App factory & entry point
├── routes/
│   └── contour.py            # Blueprint: POST /api/analyzeContour
├── services/
│   └── contour_service.py    # Business logic (validation, file handling)
├── analysis/
│   ├── terrain.py            # Terrain analysis (stub)
│   └── catchment.py          # Catchment / runoff analysis (stub)
├── utils/
│   └── kml_parser.py         # KML/KMZ parser (stub)
├── tests/
│   └── test_contour_route.py # Pytest integration tests
├── uploads/                  # Uploaded files (git-ignored)
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

## API Reference

### `POST /api/analyzeContour`

Accepts a KML or KMZ contour survey file.

**Request** — `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `file` | file | KML or KMZ file |

**Success Response** — `200 OK`

```json
{
  "success": true,
  "message": "File validated successfully.",
  "filename": "site_survey.kml"
}
```

**Error Responses**

| Status | Reason |
|--------|--------|
| `400` | Missing `file` field or empty filename |
| `415` | Unsupported file type (must be `.kml` or `.kmz`) |

**cURL example**

```bash
curl -X POST http://localhost:5000/api/analyzeContour \
     -F "file=@/path/to/survey.kml"
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Roadmap

- [ ] `utils/kml_parser.py` — Parse coordinates and polygons from KML/KMZ
- [ ] `analysis/terrain.py` — Elevation profile, slope map, contour intervals
- [ ] `analysis/catchment.py` — Catchment area, runoff coefficient, water yield
- [ ] Save uploaded files to `uploads/` with unique IDs
- [ ] Return full analysis report from `/api/analyzeContour`
