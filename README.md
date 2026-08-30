# Village Pond Planning System — Backend

A Flask-based REST API for analysing contour survey files (KML/KMZ) to assist
in planning and sizing village ponds.

---

## Project Structure

```
pond-planning/
├── app.py
├── routes/
│   └── contour.py
├── services/
│   └── contour_service.py
├── analysis/
│   ├── terrain.py              # Contour validation, metadata, slope
│   ├── dem.py                  # DEM generation
│   ├── pond.py                 # Pond candidate identification
│   ├── hydrology.py            # D8 flow direction, accumulation, channels
│   ├── catchment.py            # D8 catchment delineation & vectorization
├── utils/
│   ├── kml_parser.py
│   └── projection.py
├── tests/
│   ├── test_contour_route.py
│   ├── test_kml_parser.py
│   ├── test_terrain.py
│   ├── test_projection.py
│   ├── test_dem.py
│   ├── test_pond.py
│   └── test_hydrology.py
├── scripts/
│   ├── verify_kml.py
│   ├── visualize_dem.py
│   ├── visualize_pond.py
│   └── visualize_hydrology.py  # DEM + flow accumulation + channel network
├── uploads/
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
- Identifies the top N spatially distinct pond locations from the DEM and slope grid
- Each cell is scored by a weighted combination of normalised criteria:
  `score = 0.3 × elev_norm + 0.4 × slope_norm + 0.3 × depr_norm`  (lower score = better site)
- **Depressions:** Uses Topographic Position Index (TPI) via a 100m window to strongly prefer basin-like local depressions over flat areas
- Cells steeper than `max_slope_deg` (default 8°) and border cells are excluded
- Implements a greedy selection algorithm ensuring all returned candidates are at least `min_distance_m` (default 100m) apart
- Selection weights, slope threshold, and window sizes are all configurable

### Flow Direction, Accumulation & Channels — `analysis/hydrology.py`
- **Independent Component:** Currently operates independently from pond scoring to provide structural terrain metadata and lay groundwork for future catchment delineation.
- **Independent Component:** Currently operates independently from pond scoring to provide structural terrain metadata and lay groundwork for future catchment delineation.
- Implements the **D8 (deterministic 8-direction)** algorithm: each cell is directed toward the steepest of its 8 neighbours
- ArcGIS-standard direction codes (E=1, SE=2, S=4, SW=8, W=16, NW=32, N=64, NE=128); code 0 = pit/flat cell
- Slope computation is **fully vectorised** using numpy array slicing over a padded DEM
- **Flow accumulation** is computed via topological sort (BFS from headwater cells): each cell receives the sum of all upstream cells' values — conserves total flow count
- **Channel detection**: a configurable threshold selects high-accumulation cells as the drainage network; default is the 99th percentile (top 1 % of cells)

### Catchment Delineation — `analysis/catchment.py`
- Determines the exact upstream catchment area for a selected pour point (pond candidate)
- Uses a Breadth-First Search to recursively trace D8 flow directions backwards
- Produces a boolean raster mask of the catchment
- Converts the raster mask into a clean geographic polygon `[lon, lat]` using `contourpy`
- Computes total catchment area in square metres (`area_m2`), hectares (`area_ha`), and square kilometres (`area_km2`)

---

## Processing Pipeline

```
Upload (KML/KMZ)
      ↓
[utils/kml_parser.py]   →  list of contours
      ↓
[analysis/terrain.py]   →  validate + terrain metadata
      ↓
[utils/projection.py]   →  projected_coordinates in metres (UTM auto-selected)
      ↓
[analysis/dem.py]       →  interpolated elevation grid, saved as .npy
      ↓
[analysis/terrain.py]   →  slope per cell
      ↓
[analysis/pond.py]      →  pond_candidates list (Top N ranked sites)
      ↓
[analysis/hydrology.py] →  D8 flow direction → flow accumulation → channel mask
      ↓
[analysis/catchment.py] →  catchment polygon and area for all candidates
      ↓
API response: terrain + DEM + pond_candidates + hydrology
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
  "terrain": { "..." : "..." },
  "dem": {
    "resolution_m": 6.0, "shape": [430, 312], "nan_fraction": 0.0,
    "elevation_min": 267.0, "elevation_max": 298.0,
    "saved_to": "contours_1m_dem.npy",
    "slope": { "slope_min_deg": 0.0, "slope_max_deg": 18.4, "slope_mean_deg": 3.2 }
  },
  "pond_candidates": [
    {
      "rank": 1,
      "latitude": 21.259564, "longitude": 81.300134,
      "elevation_m": 274.1, "slope_deg": 4.3, "tpi": -4.8, "score": 0.283,
      "criteria": {
        "elevation_score": 0.18,
        "slope_score": 0.11,
        "depression_score": 0.32
      },
      "grid_row": 242, "grid_col": 343,
      "catchment": {
        "pour_point": {
          "latitude": 21.259564,
          "longitude": 81.300134
        },
        "area_m2": 24300.0,
        "area_ha": 2.43,
        "area_km2": 0.0243,
        "polygon": [
          [81.2995, 21.2601], [81.3005, 21.2605], [81.3012, 21.2592]
        ]
      }
    },
    {
      "rank": 2, "..." : "..."
    }
  ],
  "hydrology": {
    "noflow_count": 12,
    "acc_max": 8431.0,
    "acc_mean": 142.3,
    "channel_threshold": 4218.0,
    "channel_cell_count": 1345,
    "channel_fraction": 0.0073
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

174 tests across 7 test modules — all passing.

| Module | Tests | Covers |
|--------|-------|--------|
| `test_contour_route.py` | 5 | HTTP layer, status codes, full response shape |
| `test_kml_parser.py` | 20 | KML/KMZ parsing, namespaces, edge cases |
| `test_terrain.py` | 27 | Stats, interval logic, bounds, all validation errors |
| `test_projection.py` | 33 | UTM zone selection, coordinate projection, pipeline |
| `test_dem.py` | 28 | DEM structure, dimensions, elevation range, NaN, reusability |
| `test_pond.py` | 25 | Slope, Top N multi-candidate ranking, TPI depression scoring |
| `test_hydrology.py` | 32 | D8 direction codes, ramp/bowl tests, accumulation, channels |
| `test_catchment.py` | 4 | D8 upstream tracing, raster mask, area units, boundary checks |

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

To visualize flow accumulation + channel network:

```bash
python scripts/visualize_hydrology.py /path/to/your/file.kml
# outputs: hydrology.png  (DEM | log-accumulation | channel network)
```

To visualize the top 10 pond candidates:

```bash
python scripts/visualize_pond.py /path/to/your/file.kml
# outputs: pond_candidates.png
```

To visualize the delineated catchment areas for all 10 candidates:

```bash
PYTHONPATH=. python scripts/visualize_catchment.py /path/to/your/file.kml
# outputs: catchment_visualization.png
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
- [x] D8 flow direction, flow accumulation, channel detection (`analysis/hydrology.py`)
- [x] Catchment area delineation (`analysis/catchment.py`)
