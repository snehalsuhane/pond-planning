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
│   ├── waterways.py             # Existing-water screening and caching
│   ├── pond_design.py           # Excavation geometry and land/water checks
│   ├── storage.py               # Daily water balance and seasonal summaries
│   ├── sizing.py                # Pond-size recommendation engine
│   └── rainfall.py              # NASA POWER daily precipitation
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
│   ├── test_waterways.py
│   ├── test_storage.py
│   ├── test_sizing.py
│   └── ...
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
- Show estimated annual collectible runoff (m³/year) on each result card and map label, with an adjustable runoff fraction
- Explain annual runoff, pond capacity, and stored water separately; capacity is calculated in the pond-design panel, and historical stored water is estimated in its seasonal model

### Pond-Size Recommendation — `services/sizing.py`
- Evaluates a small grid of excavation lengths, widths and depths (3 × 3 × 2 = 18 cells by default) that fit the site and margin
- Runs the full historical seasonal water balance for every candidate using the same daily-rainfall and loss model as `/api/simulatePond`
- **With a target volume**: recommends the smallest evaluated design that meets or exceeds the target, plus the next step up. Reports whether historical runoff fills it and what the fill rate is across the ten modelled years
- **Without a target**: returns three labelled alternatives — small, intermediate, and large — and selects the intermediate tier as the default unless historical runoff never fills it (in which case it falls back to small). Explains the choice transparently rather than implying one uniquely correct answer
- Direct rain on each candidate's excavation rim is included in inflow; its area is subtracted from catchment runoff to avoid double-counting, matching the approach in `/api/simulatePond`
- Terrain suitability ranking is kept separate; volume is not added to the existing catchment-based score, which would effectively count catchment area twice
- Capacity uses the same trapezoidal prismatoid formula as `/api/designPond`. Side slope, freeboard and margin are fixed at the grid defaults (2:1, 0.5 m, 5 m) to keep the comparison consistent
- Loss defaults and grid dimensions are clearly labelled module-level constants, easy to adjust without touching any other logic

### Rainfall & Water Volume — `services/rainfall.py`, `services/water_volume.py`
- Retrieves daily corrected precipitation from [NASA POWER](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) for the last ten complete calendar years, then averages the annual totals
- Uses one regional rainfall estimate at the selection centroid, or the mean pond-site location for an upload without a boundary; this is coarse precipitation data, not a local rain-gauge measurement
- Requires all daily values, including leap days. Missing data is never replaced with zero
- Estimates annual collectible runoff as `rainfall_mm / 1000 × catchment_area_m2 × runoff_coefficient`
- The default runoff coefficient is **0.30** (30% of rainfall becomes runoff), an illustrative project assumption. Users can choose a value from 0 to 1 before analysis; changing it clears previous results until analysis is rerun
- Assumes uniform rainfall and that all modeled runoff reaches the collection target. The runoff calculation does not estimate pond depth, storage capacity, evaporation, seepage, or conveyance losses; this is potential annual inflow, not guaranteed yield
- Retains the existing ranking. The API retains provisional status when catchments reach the terrain edge or depend strongly on modeled depression overflow; cards and map labels explain the specific reasons. Overlapping alternatives must not be added together
- Caches complete rainfall responses for 30 days in `.cache/rainfall/` (`RAINFALL_CACHE_DIR` can override the directory). If rainfall retrieval fails, pond/catchment results remain available with volume marked unavailable

### Pond Design — `services/pond_design.py`
- Select a candidate and choose **Design this pond**. A selected land boundary is required; upload-only users can draw one and rerun analysis
- Adjust top-rim length and width, excavation depth, and clockwise orientation from grid north. Advanced assumptions include side slope (horizontal:vertical), freeboard, and a margin around the excavation
- Defaults are a 40 × 30 m rim, 2 m excavation depth, 2:1 side slopes, 0.5 m freeboard, and 5 m margin. These are illustrative project inputs, not an automatically recommended design
- Assumes level ground and a flat bottom. Bottom dimensions subtract twice the side-slope ratio times excavation depth; water-surface dimensions subtract twice the ratio times freeboard
- Calculates capacity below freeboard using `V = water_depth / 6 × (bottom_area + 4 × mid_water_depth_area + water_surface_area)`. This integrates the changing rectangular cross-section; it does not use rim area times depth
- Checks the entire rotated footprint and margin against selected land, including holes, and against mapped-water buffers. These checks do not establish ground stability, inlet/outlet suitability, or actual water availability
- Shows the rim, water surface, margin, capacity and water depth on the map. Designs that do not fit are red; unavailable water screening is amber and never reported as passing
- Design edits clear stale capacity and overlays; changing candidate, land, or terrain source clears the previous design. Recalculating uses a separate endpoint without rerunning terrain, catchments or rainfall
- Input limits (including 3 m maximum excavation depth) are project assumptions. Seasonal stored water is estimated separately; automatic size recommendations remain a later step

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
- Ranking weights are screening assumptions. Proposed pond dimensions, depth and capacity are assessed separately; rainfall–runoff estimates do not alter the ranking

### Existing-Water Screening — `services/waterways.py`
- Retrieves mapped rivers, streams, canals and water bodies through the main OpenStreetMap map API by default (`services/osm_water.py`). The selected coordinates determine the download area; no manual state downloads are needed
- Applies a 30m default exclusion buffer, accounting for mapped channel width and cell size; a natural depression overlapping the buffer is rejected as a whole
- Main-API downloads filter water tags locally and fetch missing water-relation members. Successful results are cached for 24 hours in `.cache/waterways/osm-map/`; repeated selections and smaller pond footprints reuse covering data
- Bounds-based downloads are limited to 16 requests and a 90-second lookup budget, with a 32 MB decompressed limit per response and at most two simultaneous downloads per server process. Dense-area node-limit responses trigger bounded subdivision; other failures remain explicit
- Optional `WATERWAY_PROVIDER=overpass` retains one-hour caching and tries three distinct [public Overpass instances](https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances): the main server, VK Maps, and Private.coffee. Prefers the most recent successful server within the running process and moves recently failed servers last for five minutes. An explicit `OVERPASS_ENDPOINT` override uses only that server
- Reuses fresh cached water responses when their query bounds fully contain the requested area, including smaller pond footprints. Partial coverage and expired responses are not accepted
- Returns `503` when screening is unavailable; expired or incomplete data is not used
- `WATERWAY_PROVIDER` defaults to `osm`; `OVERPASS_ENDPOINT` applies only to the optional Overpass mode. `WATERWAY_CACHE_DIR` overrides cache storage; slope and setback settings are in `app.py`
- The [main OSM map endpoint](https://wiki.openstreetmap.org/wiki/API_v0.6#Retrieving_map_data_by_bounding_box:_GET_/api/0.6/map) selects objects by vertices, so crossing or enclosing water with no vertices in the requested bounds may be absent. Results display this limitation. This bounded course-project integration is not a bulk-download service
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

Pond-size recommendation (independent pipeline, reuses cached rainfall):

[services/sizing.py]    →  candidate grid (lengths × widths × depths)
      ↓
[services/storage.py]   →  daily water balance per candidate
      ↓
[services/storage.py]   →  seasonal summary (fill rate, overflow, end-monsoon storage)
      ↓
API response: alternatives + recommended + reasoning + assumptions
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
An optional `runoff_coefficient` number from 0 to 1 defaults to 0.30.
Example request using a local JSON file containing that object:

```bash
curl -X POST http://localhost:5000/api/analyzeArea \
     -H "Content-Type: application/json" \
     --data-binary @selection.json
```

Returns `pond_candidates`, `land_selection`, `waterway_screening`, `terrain`,
`dem`, and `planning`, plus `terrain_source` with the provider and source links.
Both analysis routes also return `rainfall` (period, annual totals, mean annual mm,
and query location), `water_volume_model` (formula and assumptions), and
`water_volume` on each candidate: `{annual_m3, unit: "m3/year", status, uncertainty_reasons}`.
`uncertainty_reasons` contains code/message pairs for terrain-edge coverage and
overflow-sensitive catchments; both reasons are returned when applicable.
Volume status is `estimated`, `provisional`, or `unavailable`. Rainfall failure
keeps a successful analysis response, with `rainfall.status: "unavailable"` and
`annual_m3: null`; a valid zero runoff coefficient produces zero volume.
Invalid runoff coefficients return `400`.
`planning.terrain_buffer_m` describes the final extent and
`planning.expansion_status` reports `not_needed`, `expanded`, `limit_reached`,
or `unavailable`. Pond coordinates and catchment geometries use WGS84; catchment
areas are displayed in hectares. The selected land restricts sites, not their
upstream drainage.

Invalid/missing geometry returns `400`; an unsupported selection or no suitable
site returns `422`; unavailable public elevation or water screening returns
`503`. There is no automatic switch to unscreened results or contour terrain.

Every successful `200` response also includes a `progress_token` string and an
`X-Progress-Token` response header. The pipeline is synchronous so the token is
always in its terminal `done` state by the time the response is received, but it
can be used to poll intermediate stages if the client issues the request in a
background thread and polls separately:

```
GET /api/analysis/status/<token>
```

Returns `{"stage": "Tracing drainage\u2026", "done": false}` during analysis and
`{"stage": "done", "done": true}` on completion. `stage: "error"` and
`stage: "unknown"` (expired or invalid token) are also terminal (`done: true`).
Tokens expire after 10 minutes. Stage names: `queued`, `Retrieving elevation…`,
`Checking terrain…`, `Tracing drainage…`, `Checking mapped water…`,
`Ranking pond sites…`, `done`, `error`.

### `POST /api/designPond`

Accepts JSON with `site: {latitude, longitude}` and a required `land_area` GeoJSON
Polygon. Optional numeric inputs are `length_m` (5–500), `width_m` (5–500),
`depth_m` (0.5–3), `orientation_deg` (0–360), `side_slope` (1–4),
`freeboard_m` (0.1–1), and `margin_m` (1–30). Freeboard must be smaller than depth,
and both bottom dimensions must remain positive.

Returns `capacity_m3`, `water_depth_m`, dimensions, WGS84 `footprint`,
`water_surface` and `clearance` geometries, `checks`, assumptions and messages.
`screening_status` is `passes_checks`, `does_not_fit`, or `unverified`.
A `200` response means the calculation completed, **not** that the design passed:
inspect `screening_status`. During a water-provider outage the geometry and
capacity remain available, with `avoids_mapped_water: null` and status `unverified`.
If land containment fails, water screening is skipped. Invalid inputs return `400`.
The endpoint screens the submitted design; it does not generate or rank a site.

### `POST /api/simulatePond`

Accepts the same design inputs as `/api/designPond`, plus a `catchment` GeoJSON
Polygon or MultiPolygon from the terrain result. Optional inputs are
`runoff_coefficient` (0–1, default 0.3), `evaporation_mm_day` (0–20, default 4),
`seepage_mm_day` (0–20, default 1), `demand_m3_day` (0–100000, default 0), and
`initial_storage_fraction` (0–1, default 0). Input bounds are project limits.

Reuses the cached NASA POWER daily rainfall at the selected land's centroid.
Returns `capacity_m3`, rainfall metadata, assumptions, monthly and annual balances,
and period totals with a `balance_residual_m3` conservation check. Each day adds
catchment runoff and direct rain, spills water above capacity, subtracts evaporation
and seepage, then supplies water use up to the available volume. Loss depths apply
to the changing water surface after inflow. Rain falling within the excavation rim
is assumed to drain entirely into the pond; its overlap is removed from catchment
runoff to avoid double counting. Storage carries between years without resetting.

The UI offers this after a design passes map checks. Select a historical year and
month to inspect the storage chart and month-end volume on the pond's map label.
Reports include years reaching capacity, overflow and unmet water use. Editing
assumptions clears stale storage; editing dimensions or changing sites clears the
design and simulation. This endpoint recalculates geometry but does not repeat
terrain analysis or water screening, and does not certify a submitted site's suitability.
Invalid inputs return `400`; unavailable rainfall returns `503` without inventing
storage values. The existing design remains visible when simulation fails.

Loss defaults are illustrative, not local measurements. Results inherit catchment
uncertainty and are historical scenarios, not forecasts or guaranteed yields.

### `POST /api/suggestSize`

Accepts the same required inputs as `/api/simulatePond` — `site`, `land_area`,
and a `catchment` GeoJSON Polygon or MultiPolygon — plus the same optional
hydrological parameters (`runoff_coefficient`, `evaporation_mm_day`,
`seepage_mm_day`, `demand_m3_day`, `initial_storage_fraction`).

An optional `target_volume_m3` (non-negative float) switches the response mode:

**With `target_volume_m3`** (`mode: "target"`):
- Returns the smallest evaluated design that reaches the requested capacity, labelled
  `recommended`, plus the next step up labelled `next_step_up` if one exists
- Reports whether historical runoff fills it and the fill rate across modelled years
- When no evaluated design meets the target, returns the largest available with label
  `largest_available` and `recommended: null`

**Without `target_volume_m3`** (`mode: "alternatives"`):
- Returns up to three labelled alternatives: `small`, `intermediate`, and `large`
- Selects `intermediate` as the default (`recommended`) unless historical runoff
  never fills it, in which case it falls back to `small`
- Provides a `reasoning` string that explains the default choice transparently

Each alternative contains: `label`, `length_m`, `width_m`, `depth_m`,
`capacity_m3`, `fill_rate_pct`, `years_pond_filled`, `mean_annual_overflow_m3`,
`mean_end_monsoon_storage_m3`, `mean_annual_inflow_m3`, `runoff_supports_fill`,
and a full `seasonal` breakdown matching the `/api/simulatePond` format.

Response also includes `assumptions` (grid dimensions, loss values, formula note),
`rainfall` provenance, and `runoff_area_m2` (catchment minus pond footprint).

The evaluation uses a fixed grid of 3 lengths × 3 widths × 2 depths (18 cells).
Side slope (2:1), freeboard (0.5 m) and margin (5 m) are fixed grid constants.
Terrain suitability is not re-ranked; the sizing pipeline is deliberately independent
of the catchment-based score to avoid counting catchment area twice.

Invalid inputs return `400`; unavailable rainfall returns `503`.

### `POST /api/analyzeContour`

Accepts a KML or KMZ contour survey file, parses it, validates the terrain
data, projects coordinates, and returns terrain metadata, ranked pond locations
and catchment information.

**Request** — `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `contour_map` | file | `.kml` or `.kmz` survey file |
| `runoff_coefficient` | optional number | Fraction from 0 to 1; defaults to 0.30 |
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

444 tests across 20 test modules.

| Module | Tests | Covers |
|--------|-------|--------|
| `test_contour_route.py` | 18 | HTTP layer, status codes, full response shape |
| `test_kml_parser.py` | 22 | KML/KMZ parsing, namespaces, edge cases |
| `test_terrain.py` | 25 | Stats, interval logic, bounds, all validation errors |
| `test_projection.py` | 31 | UTM zone selection, coordinate projection, pipeline |
| `test_dem.py` | 37 | DEM structure, dimensions, elevation range, NaN, reusability |
| `test_pond.py` | 21 | Slope, collection targets, catchments, ranking and exclusions |
| `test_hydrology.py` | 44 | D8 direction codes, filling, flat routing, accumulation, channels |
| `test_catchment.py` | 9 | D8 upstream tracing, raster mask, area units, polygon WGS84 bounds |
| `test_waterways.py` | 16 | Water buffers, geometry, incomplete results, retries and caching |
| `test_osm_water.py` | 11 | Main OSM downloads, relation completion, bounded subdivision, provider selection and coverage caching |
| `test_area_route.py` | 16 | Map-only requests, coverage expansion, selected-land containment, runoff input |
| `test_land_selection.py` | 18 | Polygon validation and whole-cell site containment |
| `test_elevation.py` | 10 | Public elevation windows, projection, seams, limits and caching |
| `test_places.py` | 11 | Submitted location search, caching, rate limiting and provider failures |
| `test_rainfall.py` | 11 | Complete calendar coverage, units, invalid days, cache and provider failures |
| `test_water_volume.py` | 17 | Annual runoff formula, coefficient limits, provisional status and unavailable rainfall |
| `test_storage.py` | 47 | Daily water conservation, leap years, carry-over, losses, dry-season drainage to empty, demand capping, direct rain, seasonal aggregation, fill rate, end-monsoon storage, custom seasons and API integration |
| `test_pond_design.py` | 17 | Sloped capacity, freeboard, rotation, full-footprint containment, water intersections and API validation |
| `test_sizing.py` | 50 | Capacity formula, candidate grid, validation, alternatives mode, target mode, infeasible targets, HTTP route |
| `test_progress.py` | 13 | Token lifecycle, stage updates, expiry, eviction, HTTP polling endpoint, analyzeArea token embedding |

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
- [x] Historical rainfall and annual runoff estimates with map labels
- [x] Interactive pond footprint, proposed depth, capacity and land/water fit checks
- [x] Historical seasonal storage with adjustable losses and water use
- [x] Pond-size recommendation: candidate grid, water-balance evaluation, target and alternatives modes (`services/sizing.py`)
- [ ] Soil suitability and field validation
