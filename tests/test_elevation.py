"""Public raster coverage, projection, orientation and repeat-request caching."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from services.elevation import (fetch_elevation, read_window, make_grid, tile_url,
                                ElevationDataError, ElevationSelectionError)


def test_hemisphere_tile_names():
    assert 'N21_00_E081_00' in tile_url(21, 81)
    assert 'S04_00_W073_00' in tile_url(-4, -73)


@pytest.mark.parametrize('land', [box(80, 20, 81, 21), box(80, 85, 80.001, 85.001),
                                 box(-179, 0, 179, .001)])
def test_large_or_unsupported_areas_are_rejected(land):
    with pytest.raises(ElevationSelectionError):
        make_grid(land)


def test_cell_budget_is_checked_before_fetching():
    with pytest.raises(ElevationSelectionError, match='processing limit'):
        make_grid(box(81, 21, 81.001, 21.001), max_cells=10)


def test_cache_and_adapter_reverse_north_up_rows(monkeypatch, tmp_path):
    calls = []
    def reader(grid):
        calls.append(grid)
        return np.tile(np.arange(grid['height'], dtype='float32')[:, None], (1, grid['width'])), ['tile']
    monkeypatch.setattr('services.elevation.read_window', reader)
    land = box(81.1, 21.1, 81.101, 21.101)
    first, epsg, metadata = fetch_elevation(land, cache_dir=tmp_path)
    second, _, cached = fetch_elevation(land, cache_dir=tmp_path)
    assert epsg == 32644 and not metadata['cached'] and cached['cached']
    assert len(calls) == 1
    assert first['dem'][0, 0] > first['dem'][-1, 0]
    assert first['y_coords'][0] < first['y_coords'][-1]
    assert first['x_coords'][0] < first['x_coords'][-1]
    assert first['resolution_m'] == 30
    assert first['valid_mask'].all()
    np.testing.assert_array_equal(first['dem'], second['dem'])
    fetch_elevation(land, buffer_m=4000, cache_dir=tmp_path)
    assert len(calls) == 2


def test_corrupt_cache_is_refetched(monkeypatch, tmp_path):
    def reader(grid):
        return np.ones((grid['height'], grid['width']), dtype='float32'), ['tile']
    monkeypatch.setattr('services.elevation.read_window', reader)
    land = box(81.1, 21.1, 81.101, 21.101)
    fetch_elevation(land, cache_dir=tmp_path)
    next(tmp_path.glob('*.npz')).write_bytes(b'broken archive')
    _, _, metadata = fetch_elevation(land, cache_dir=tmp_path)
    assert not metadata['cached']


def test_reprojection_across_tile_seam_keeps_complete_coverage(monkeypatch, tmp_path):
    real_open = rasterio.open
    paths = {}
    for lon in (80, 81):
        west = 80.99 if lon == 80 else 81.
        transform = from_origin(west, 21.26, .00025, .00025)
        x = west + (np.arange(40)+.5)*.00025
        data = np.tile(200+(x-81)*1000, (80, 1)).astype('float32')
        path = tmp_path/f'{lon}.tif'
        with real_open(path, 'w', driver='GTiff', width=40, height=80, count=1,
                       dtype='float32', crs='EPSG:4326', transform=transform) as dst:
            dst.write(data, 1)
        paths[lon] = path
    def open_tile(url):
        return real_open(paths[80 if 'E080' in url else 81])
    monkeypatch.setattr(rasterio, 'open', open_tile)
    grid = make_grid(box(80.998, 21.248, 81.002, 21.252), buffer_m=0)
    array, sources = read_window(grid)
    assert len(sources) == 2
    assert np.isfinite(array).all()
    assert np.max(np.abs(np.diff(array, axis=1))) < 1
    assert 196 < array.min() < array.max() < 204


def test_missing_tiles_are_not_zero_elevation(monkeypatch):
    def missing(*args, **kwargs):
        raise rasterio.errors.RasterioIOError('HTTP response code: 404')
    monkeypatch.setattr(rasterio, 'open', missing)
    with pytest.raises(ElevationDataError, match='does not fully cover'):
        read_window(make_grid(box(81.1, 21.1, 81.101, 21.101)))


def test_network_failure_has_actionable_error(monkeypatch):
    def fail(*args, **kwargs):
        raise rasterio.errors.RasterioIOError('Network is unreachable')
    monkeypatch.setattr(rasterio, 'open', fail)
    with pytest.raises(ElevationDataError, match='Check the server connection'):
        read_window(make_grid(box(81.1, 21.1, 81.101, 21.101)))
