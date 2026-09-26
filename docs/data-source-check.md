# Public data source check

Stage 1 was checked on 27 September 2026 (IST), using `contours_1m.kml`
to derive the sample region. The purpose is to verify that public terrain and
precipitation data can support the course project's map-based workflow.

## Results

| Check | Result |
| --- | --- |
| Region | Derived from the input coordinates; center approximately 21.251702 N, 81.297026 E |
| Public terrain | Copernicus GLO-30 tile available without an API key |
| Terrain extent | Sample bounds with a 2 km surrounding buffer |
| Projected raster | EPSG:32644, 30 m cells, 221 rows by 242 columns |
| Coverage | All 53,482 output cells had finite elevation values |
| Terrain elevations | Approximately 269.0–298.7 m across the buffered region |
| Existing analysis | Slope calculation and hydrology completed successfully |
| Historical precipitation | NASA POWER returned all 3,653 daily records for 2016–2025 |
| Mean annual precipitation | 1,408.96 mm/year over those ten complete years |
| Open-Meteo | Both attempts returned HTTP 429; the second response explicitly reported an exhausted daily API limit |

## Choices for the next stages

Use **Copernicus GLO-30** for public terrain and **NASA POWER** for historical
precipitation. Both live retrievals worked for this region without credentials.
NASA POWER is already an alternative in the HLD. Keep these as explicit sources;
an automatic multi-provider fallback system is unnecessary for this project.

The scratch terrain adapter reads a window from the remote GeoTIFF, reprojects
it to a local metric grid, and reverses the raster rows to match the existing
analysis code's south-to-north row order. It then calls the existing slope and
hydrology functions without changing their algorithms. The orientation,
finite coverage, and flow-accumulation bounds were checked.

The NASA request uses `PRECTOTCORR`, community `AG`, daily intervals, and UTC.
The response identifies MERRA-2 as its source and reports corrected precipitation
in mm/day. Summing each year's daily values gives annual millimeters; averaging
the ten annual totals gives the value above. Checks covered the exact date set,
leap days, units, missing values, negative values, and provider warnings.

For the course demonstration, this regional precipitation can serve as the
rainfall input for a simple runoff estimate. Show the source, averaging period,
and assumed runoff coefficient alongside the result. It is an estimate of
potential inflow, not pond storage capacity. Snow-dominated regions would need
additional handling in a later phase.

Public elevation is a surface model and has coarser detail than the uploaded
contours. At the contour vertices, sampled public elevations were about 5.06 m
lower in the median. This comparison does not establish which source is more
accurate; retain separate terrain modes rather than blending them. Public
terrain is sufficient for an explainable preliminary course demonstration.

The 2 km buffer verifies retrieval and processing only. It does not establish
complete catchments, validate pond locations, or implement land-selection
filtering; those remain tasks for the planned integration stages.

## Local experiment

The experiment and raw results are under the already-ignored `scratch/stage1/`:

- `check_sources.py`: accepts a contour file and derives its region automatically.
- `terrain_30m.tif` and `terrain_summary.json`: projected terrain and check results.
- `nasa_rainfall_raw.json` and `nasa_rainfall_summary.json`: source data and totals.
- `rainfall_failure.json`: Open-Meteo's rate-limit response.

With the existing project environment and Rasterio installed, rerun from the
repository root:

```bash
python scratch/stage1/check_sources.py contours_1m.kml terrain
python scratch/stage1/check_sources.py contours_1m.kml nasa_rainfall
```

Rasterio 1.5.1 was installed temporarily under `/tmp/pond-stage1-deps` for this
check. The terrain command in this session used
`PYTHONPATH=/tmp/pond-stage1-deps /home/snehal/my_env/bin/python`.
The application, existing requirements, README, and ranking code were unchanged.
Scratch artifacts are local development evidence, not submission scripts.

## Source documentation

- [Copernicus public raster format, access, and license](https://copernicus-dem-30m.s3.amazonaws.com/readme.html)
- [NASA POWER daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/)
- [NASA POWER precipitation methodology](https://power.larc.nasa.gov/docs/methodology/meteorology/precipitation/)
- [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api)
