# Village Pond Planning System — Backend

A Flask-based REST API for analysing contour survey files (KML/KMZ) to assist
in identifying pond locations and estimating their catchment areas.
A browser interface provides contour uploads, land-boundary drawing, and
interactive exploration of the suggested pond locations and catchments.
Land can also be analyzed directly on the map using public elevation data,
without uploading a contour file.

---

## Project Structure

```
pond-planning/
├── app.py
├── templates/
│   └── index.html               # Map and analysis interface
├── static/
│   ├── css/app.css
│   └── js/app.js
├── routes/
│   └── contour.py
├── services/
│   ├── contour_service.py
│   ├── area_service.py          # Selected-land analysis using public terrain
│   ├── elevation.py             # Copernicus raster retrieval and caching
│   └── waterways.py             # Existing-water screening and caching
├── analysis/
│   ├── terrain.py              # Contour validation, metadata, slope
│   ├── dem.py                  # DEM generation
│   ├── pond.py                 # Pond candidate identification
│   ├── hydrology.py            # D8 flow direction, accumulation, channels
│   ├── catchment.py            # D8 catchment delineation & vectorization
│   └── raster_geometry.py      # Cell-edge polygons, holes and multipart geometry
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
│   ├── test_hydrology.py
│   ├── test_catchment.py
│   └── test_waterways.py
├── scripts/
│   ├── verify_kml.py
│   ├── visualize_dem.py
│   ├── visualize_pond.py
│   └── visualize_catchment.py
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

The API will be available at `http://localhost:5000`. Water screening requires
internet access or a matching response cached within the last hour.
Open the same address in a browser. Choose public elevation and draw your land,
or switch to a contour survey to upload a KML/KMZ file. You can pan/zoom the map
use the place-name search bar, or use **Go to coordinates** to locate the land.
The map libraries and OpenStreetMap base map also require internet access.

---

## Implemented Features

### Map Interface
- Search for a village, town, or landmark, then choose a matching location to move the map
- Cursors distinguish panning (grab/grabbing), drawing (crosshair), editing (move), removal, clickable markers, and zoom controls
- Analyze drawn land using public elevation without a file, or switch to contour upload
- Upload a KML/KMZ survey and analyze it through the existing API
- Preview KML contour lines before analysis; KMZ results locate the map after analysis
- Draw, edit, or delete one land boundary, with its approximate area shown in hectares
- A drawn boundary restricts pond sites to the selected land; upstream catchments still use the full survey and can extend outside that boundary
- Natural depression targets must fit entirely inside the selected land; editing or deleting the boundary clears old results until analysis is rerun
- Reports partial survey coverage and marks catchments reaching the survey edge as provisional; uploaded terrain cannot be extended beyond its available coverage
- Explore up to five numbered pond options, with linked map markers and result cards
- Display one catchment at a time, preserving polygon holes and multipart geometry; optionally compare all outlines
- Show catchment hectares, local slope, coordinates, and survey-boundary/overflow qualifications
- Provide upload validation, loading feedback, and retryable error messages on desktop and mobile
- Show the terrain source, resolution, surrounding extent, and coverage qualifications; the analyzed terrain outline is available in the map layer control
- Rainfall-based water volume is planned for the next integration stage

### Public Elevation — `services/elevation.py`
- Retrieves [Copernicus GLO-30](https://copernicus-dem-30m.s3.amazonaws.com/readme.html) elevation windows from public Cloud Optimized GeoTIFFs; no API key is needed
- Projects elevation into a local UTM grid at 30 m spacing, preserving the row orientation expected by the existing slope and drainage code
- Starts with 2 km of surrounding terrain; expands once to 4 km if a returned catchment touches the analysis edge
- Uses the same pond ranking, land containment, catchment tracing, and mapped-water screening as the contour workflow
- Still marks edge-reaching catchments as provisional at the expansion limit. If expansion fails, retains only the earlier screened result and reports the incomplete expansion
- Limits one selection to 2,500 hectares and a maximum dimension of 15 km; the raster also has a one-million-cell processing limit
- Supports local areas between 80°S and 84°N; date-line crossings are not supported
- Caches complete projected windows in `.cache/terrain/` (`TERRAIN_CACHE_DIR` can override the directory). Missing/incomplete terrain fails explicitly instead of being filled with invented elevation
- Public surface elevation is coarser than survey contours and can include buildings or vegetation. Use it for preliminary comparison, with source attribution shown alongside results

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
- Cells outside the contour vertices' convex hull are excluded from hydrological analysis
- Grid resolution is **auto-derived** from the data extent and snapped to the contour interval — no hardcoded values
- The DEM array is saved as a `.npy` file in `uploads/` alongside the source KML for downstream steps

### Slope Calculation — `analysis/terrain.py`
- Computes per-cell slope in degrees using `numpy.gradient` with a central-difference scheme
- Formula: `slope = arctan( sqrt( (dZ/dX)² + (dZ/dY)² ) )`
- Returns slope grid (same shape as DEM) plus summary stats: min, max, mean
- Slope summary is included in the API response under `dem.slope`

### Pond Candidate Identification — `analysis/pond.py`
- Identifies up to five spatially distinct pond locations from natural depressions and drainage outlets
- Each collection target is scored by a weighted combination of normalised criteria:
  `score = 0.75 × drainage_penalty + 0.25 × slope_penalty`  (lower score = better site)
- **Drainage:** `1 - log(1 + N) / log(1 + Nmax)`, where N is the connected catchment cell count and Nmax is the largest eligible catchment
- **Slope:** Mean slope over approximately 30m (at least 3 × 3 cells), divided by `max_slope_deg` and capped at 1
- Cells steeper than `max_slope_deg` (default 8°), within the survey-edge setback (default 100m), or on mapped water are excluded
- Implements a greedy selection algorithm ensuring all returned candidates are at least `min_distance_m` (default 100m) apart
- Near-duplicate catchments (intersection-over-union ≥ 80%) are skipped; remaining alternatives can overlap and should not be added as independent supplies
- Ranking weights are screening assumptions. Pond shape, depth, capacity and rainfall–runoff calculations are outside this phase

### Existing-Water Screening — `services/waterways.py`
- Retrieves mapped rivers, streams, canals and water bodies from OpenStreetMap through Overpass
- Applies a 30m default exclusion buffer, accounting for mapped channel width and cell size; a natural depression overlapping the buffer is rejected as a whole
- Retries transient failures up to three times and caches complete responses for one hour in `.cache/waterways/`
- If the default Overpass server is unreachable, remaining attempts use the [VK Maps public instance](https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances); the total remains three attempts. An explicit `OVERPASS_ENDPOINT` override uses only that server
- Returns `503` when screening is unavailable; expired or incomplete data is not used
- `OVERPASS_ENDPOINT` and `WATERWAY_CACHE_DIR` are environment overrides; slope and setback settings are in `app.py`
- Mapping may be incomplete. Water buffers exclude candidate locations but do not alter terrain routing. © OpenStreetMap contributors

### Flow Direction, Accumulation & Channels — `analysis/hydrology.py`
- **Integrated pipeline step:** Runs immediately after DEM/slope computation and feeds the catchment delineation module.
- **Depression filling:** Priority-Flood raises depressions to their spill levels in the routing model; equal-elevation cells are routed towards outlets
- Implements the **D8 (deterministic 8-direction)** algorithm: each cell is directed toward the steepest of its 8 neighbours
- ArcGIS-standard direction codes (E=1, SE=2, S=4, SW=8, W=16, NW=32, N=64, NE=128); code 0 = pit/flat cell
- Slope computation is **fully vectorised** using numpy array slicing over a padded DEM
- **Flow accumulation** is computed via topological sort (BFS from headwater cells): each cell receives the sum of all upstream cells' values — conserves total flow count and rejects cycles
- **Channel detection**: a configurable threshold selects high-accumulation cells as the drainage network; default is the 99th percentile (top 1 % of cells)

### Catchment Delineation — `analysis/catchment.py`
- Determines the modeled upstream catchment area for a drainage outlet or an entire natural depression (collection target)
- Uses a Breadth-First Search to recursively trace D8 flow directions backwards
- Produces a boolean raster mask of the catchment
- Converts the raster mask into cell-edge geographic polygons `[lon, lat]` using Shapely; GeoJSON `geometry` preserves holes and multipart boundaries
- Computes total catchment area in square metres (`area_m2`), hectares (`area_ha`), and square kilometres (`area_km2`)
- Connected drainage assumes upstream depressions can spill; an unfilled-terrain comparison reports sensitivity to this assumption, not guaranteed water supply
- For a natural depression, the displayed location represents the collection region; an outlet marker can lie at its catchment's downstream edge
- Flags catchments touching the available terrain boundary and upstream mapped-water-buffer overlap; the convex hull is not a verified survey boundary

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
[analysis/hydrology.py] →  D8 flow direction → flow accumulation → channel mask
      ↓
[services/waterways.py] →  mapped-water exclusion mask
      ↓
[analysis/pond.py]      →  collection targets
      ↓
[analysis/catchment.py] →  catchment polygon and area for all targets
      ↓
[analysis/pond.py]      →  top 5 ranked, spatially distinct alternatives
      ↓
API response: terrain + DEM + pond_candidates + hydrology + waterway_screening + planning
```

---

## API Reference

### `GET /api/places/search?q=...`

Returns up to five matching locations, each with a label, latitude, longitude,
and optional map bounds. Search is submitted explicitly with Enter or the
Search button, not on every keystroke. Results navigate the map; they do not
select a land boundary automatically.

The [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
requires attribution, identified requests, no autocomplete, and at most one
request per second. The single-process Flask app caches queries for 24 hours
and serializes provider requests to meet that limit. `NOMINATIM_ENDPOINT` can
override the search URL. Multiple worker processes would require a shared
rate limiter before deployment. Invalid queries return `400`; provider outages
return `503`, while manual map navigation and coordinate entry remain usable.

### `POST /api/analyzeArea`

Accepts JSON containing a required `land_area` GeoJSON Polygon (or Polygon
Feature), with coordinates in `[longitude, latitude]` order. No upload is needed.
Example request using a local JSON file containing that object:

```bash
curl -X POST http://localhost:5000/api/analyzeArea \
     -H "Content-Type: application/json" \
     --data-binary @selection.json
```

Returns `pond_candidates`, `land_selection`, `waterway_screening`, `terrain`,
`dem`, and `planning`, plus `terrain_source` with the provider and source links.
`planning.terrain_buffer_m` describes the final extent and
`planning.expansion_status` reports `not_needed`, `expanded`, `limit_reached`,
or `unavailable`. Pond coordinates and catchment geometries use WGS84; catchment
areas are displayed in hectares. The selected land restricts sites, not their
upstream drainage.

Invalid/missing geometry returns `400`; an unsupported selection or no suitable
site returns `422`; unavailable public elevation or water screening returns
`503`. There is no automatic switch to unscreened results or contour terrain.

### `POST /api/analyzeContour`

Accepts a KML or KMZ contour survey file, parses it, validates the terrain
data, projects coordinates, and returns terrain metadata, ranked pond locations
and catchment information.

**Request** — `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `contour_map` | file | `.kml` or `.kmz` survey file |
| `land_area` | optional JSON string | WGS84 GeoJSON Polygon (or Polygon Feature), using `[longitude, latitude]` coordinates; restricts collection targets, not upstream catchments |

**Success Response** — `200 OK`

Selected fields from the sample response; geometry and metadata are abbreviated.
`geometry` contains the complete boundary; `polygon` contains the largest exterior
ring. Only drainage-outlet catchments include `pour_point`. Internal raster
indices are not included in the API response.
`land_selection` reports the submitted geometry, its area in hectares, and the
fraction covered by survey terrain (or is `null` for a full-survey analysis).
The survey-edge setback still applies to the terrain boundary, not the selected
land boundary. Entire raster cells must fit inside the land for site selection.

```json
{
  "status": "success",
  "filename": "contours_1m.kml",
  "terrain": { "..." : "..." },
  "dem": { "..." : "..." },
  "pond_candidates": [
    {
      "rank": 1,
      "latitude": 21.250342121857123, "longitude": 81.30335412600483,
      "collection_type": "natural_depression",
      "elevation_m": 283.0, "slope_deg": 0.0, "score": 0.054256,
      "criteria": {
        "drainage_score": 0.049873,
        "slope_score": 0.004384
      },
      "unfilled_contributing_area_ha": 1.5768,
      "catchment": {
        "collection_point": {
          "latitude": 21.250342121857123,
          "longitude": 81.30335412600483
        },
        "area_m2": 973584.0,
        "area_ha": 97.3584,
        "area_km2": 0.973584,
        "boundary_truncated": false,
        "geometry": {
          "...": "..."
        },
        "polygon": [
          "..."
        ]
      },
      "assessment": {
        "...": "..."
      }
    },
    {
      "rank": 2, "..." : "..."
    }
  ],
  "hydrology": { "..." : "..." },
  "waterway_screening": { "..." : "..." },
  "planning": {
    "ranking_version": "collection_targets_v3",
    "...": "..."
  }
}
```

**Error Responses**

| Status | Reason |
|--------|--------|
| `400` | Missing `contour_map` field, empty filename, or invalid `land_area` geometry |
| `415` | Unsupported file type (must be `.kml` or `.kmz`) |
| `422` | File is malformed, fails terrain validation, has no suitable collection target, or selected land contains no complete covered terrain cells |
| `503` | Existing-water screening is unavailable or incomplete |

**cURL example**

```bash
curl -X POST http://localhost:5000/api/analyzeContour \
     -F "contour_map=@/path/to/survey.kml"
```

---

## Running Tests

```bash
python -m pytest tests/ -v
```

207 tests across 9 test modules — all passing.

| Module | Tests | Covers |
|--------|-------|--------|
| `test_contour_route.py` | 7 | HTTP layer, status codes, full response shape |
| `test_kml_parser.py` | 22 | KML/KMZ parsing, namespaces, edge cases |
| `test_terrain.py` | 25 | Stats, interval logic, bounds, all validation errors |
| `test_projection.py` | 31 | UTM zone selection, coordinate projection, pipeline |
| `test_dem.py` | 37 | DEM structure, dimensions, elevation range, NaN, reusability |
| `test_pond.py` | 21 | Slope, collection targets, catchments, ranking and exclusions |
| `test_hydrology.py` | 44 | D8 direction codes, filling, flat routing, accumulation, channels |
| `test_catchment.py` | 9 | D8 upstream tracing, raster mask, area units, polygon WGS84 bounds |
| `test_waterways.py` | 11 | Water buffers, geometry, incomplete results, retries and caching |

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

To visualize the top 5 pond candidates:

```bash
python scripts/visualize_pond.py /path/to/your/file.kml
# outputs: catchment_visualization.png  (locations overview | separate catchment panels)
```

To visualize the delineated catchment areas for all 5 candidates:

```bash
PYTHONPATH=. python scripts/visualize_catchment.py /path/to/your/file.kml
# outputs: catchment_visualization.png
```

The catchment panels use the same extent and scale, show connected and unfilled
areas in hectares, and flag catchments reaching the data boundary. Survey files
and generated plots are kept locally and excluded from Git.

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

- [x] Existing-water screening and separate catchment visualisations
- [ ] Rainfall–runoff estimates, soil suitability, pond sizing and field validation
