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
│   └── contour_service.py      # Full upload pipeline
├── analysis/
│   ├── terrain.py              # Contour validation & terrain metadata
│   ├── dem.py                  # DEM generation from projected contours
│   └── catchment.py            # Catchment / runoff analysis (stub)
├── utils/
│   ├── kml_parser.py           # KML/KMZ parser
│   └── projection.py           # Coordinate projection (lon/lat → metres)
├── tests/
│   ├── test_contour_route.py   # HTTP layer integration tests
│   ├── test_kml_parser.py      # KML/KMZ parser unit tests
│   ├── test_terrain.py         # Terrain analysis unit tests
│   ├── test_projection.py      # Coordinate projection unit tests
│   └── test_dem.py             # DEM generation unit tests
├── verify_kml.py               # Manual KML + terrain verification CLI
├── visualize_dem.py            # DEM visualization CLI (outputs PNG)
├── uploads/                    # Uploaded files + generated .npy DEMs
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

### DEM Generation — `analysis/dem.py`
- Converts projected contour lines into a **continuous elevation surface** on a regular grid
- **Linear interpolation** (`scipy.griddata`) fills the grid inside the convex hull; **nearest-neighbour fill** covers edges, ensuring zero NaN cells
- Grid resolution is **auto-derived** from the data extent and snapped to the contour interval — no hardcoded values
- The DEM array is saved as a `.npy` file in `uploads/` alongside the source KML for downstream steps

### Slope Calculation — `analysis/terrain.py`
- Computes per-cell slope in degrees using `numpy.gradient` with a central-difference scheme
- Formula: `slope = arctan( sqrt( (dZ/dX)² + (dZ/dY)² ) )`
- Returns slope grid (same shape as DEM) plus summary stats: min, max, mean
- Slope summary is included in the API response under `dem.slope`

### Pond Candidate Identification — `analysis/pond.py`
- Identifies the most suitable pond location from the DEM + slope grid algorithmically
- Each cell is scored by a weighted combination of normalised elevation and normalised slope:
  `score = 0.6 × elev_norm + 0.4 × slope_norm`  (lower score = better site)
- Cells steeper than `max_slope_deg` (default 8°) and border cells are excluded before selection
- The best-scoring cell's projected (X, Y) coordinates are back-projected to geographic (lat, lon)
- Selection weights and slope threshold are configurable — no coordinates are hardcoded

---

## Processing Pipeline

```
Upload (KML/KMZ)
      ↓
[utils/kml_parser.py]   →  list of contours  {id, elevation, coordinates: [[lon, lat]]}
      ↓
[analysis/terrain.py]   →  validate + extract terrain metadata
      ↓
[utils/projection.py]   →  add projected_coordinates: [[X_m, Y_m]]  (UTM auto-selected)
      ↓
[analysis/dem.py]       →  interpolate regular elevation grid (DEM), save as .npy
      ↓
[analysis/terrain.py]   →  calculate slope for each DEM cell
      ↓
[analysis/pond.py]      →  score cells, apply masks, select best candidate
      ↓
API response: terrain + CRS + DEM + slope summary + pond_site
      ↓
[Next] Catchment area delineation
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
    "bounds": { "min_lon": 81.28, "min_lat": 21.24, "max_lon": 81.31, "max_lat": 21.26 },
    "crs": { "epsg": 32644, "name": "WGS 84 / UTM zone 44N", "unit": "metre" }
  },
  "dem": {
    "resolution_m": 6.0,
    "shape": [430, 312],
    "nan_fraction": 0.0,
    "elevation_min": 267.0,
    "elevation_max": 298.0,
    "bounds": { "min_x": 360241.5, "min_y": 2349822.4, "max_x": 363121.6, "max_y": 2352406.1 },
    "saved_to": "contours_1m_dem.npy",
    "slope": {
      "slope_min_deg": 0.0,
      "slope_max_deg": 18.4,
      "slope_mean_deg": 3.2
    }
  },
  "pond_site": {
    "latitude": 21.23982,
    "longitude": 81.29134,
    "elevation_m": 267.0,
    "slope_deg": 1.4,
    "grid_row": 2,
    "grid_col": 47
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

135 tests across 6 test modules — all passing.

| Module | Tests | Covers |
|--------|-------|--------|
| `test_contour_route.py` | 5 | HTTP layer, status codes, full response shape |
| `test_kml_parser.py` | 20 | KML/KMZ parsing, namespaces, edge cases |
| `test_terrain.py` | 27 | Stats, interval logic, bounds, all validation errors |
| `test_projection.py` | 33 | UTM zone selection, coordinate projection, pipeline |
| `test_dem.py` | 28 | DEM structure, dimensions, elevation range, NaN, reusability |
| `test_pond.py` | 22 | Slope values, candidate structure, bounds, data-independence |

---

## Manual Verification

To parse and validate a KML file:

```bash
python scripts/verify_kml.py /path/to/your/file.kml
```

To visually validate the DEM:

```bash
python scripts/visualize_dem.py /path/to/your/file.kml
# outputs: dem_visualization.png
```

To visualize terrain + slope + pond candidate (recommended after any change):

```bash
python scripts/visualize_pond.py /path/to/your/file.kml
# outputs: pond_candidate.png  (3-panel: DEM, slope map, zoomed candidate view)
```

---

## Roadmap

- [x] Flask API skeleton with file upload endpoint
- [x] KML/KMZ parser (`utils/kml_parser.py`)
- [x] Terrain validation & metadata (`analysis/terrain.py`)
- [x] Coordinate projection to UTM metres (`utils/projection.py`)
- [x] DEM generation from projected contour data (`analysis/dem.py`)
- [x] Slope calculation from DEM (`analysis/terrain.py`)
- [x] Pond candidate identification (`analysis/pond.py`)
- [ ] Catchment area delineation (`analysis/catchment.py`)
- [ ] Water yield estimation
