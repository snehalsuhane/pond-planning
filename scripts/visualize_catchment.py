"""Inspect pond locations and catchments directly from a KML/KMZ contour map."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from pyproj import Transformer
from utils.kml_parser import parse
from utils.projection import project_contours
from analysis.dem import generate_dem
from analysis.terrain import calculate_slope
from analysis.hydrology import run_hydrology
from analysis.pond import rank_pond_candidates
from services.waterways import screen_waterways, WaterwayDataError


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: visualize_catchment.py survey.kml [output.png]')
    contours, crs = project_contours(parse(sys.argv[1]))
    d = generate_dem(contours)
    slopes = calculate_slope(d)
    hydro = run_hydrology(d)
    try:
        water, _ = screen_waterways(d, crs['epsg'])
    except WaterwayDataError as exc:
        raise SystemExit(f'Cannot visualize pond choices: {exc}') from None
    result = rank_pond_candidates(d, slopes, crs['epsg'], hydrology=hydro,
                                 exclusion_mask=water, edge_setback_m=100, include_masks=True)
    sites = result['pond_candidates']
    print('Drainage-ranking reference maximum (ha):', result['drainage_saturation_ha'])
    print('Rank | Catchment (ha)')
    for site in sites:
        print(f"{site['rank']:4} | {site['catchment']['area_ha']:14.4f}")
    gx, gy, res = d['x_coords'], d['y_coords'], d['resolution_m']
    extent = np.array([gx[0]-res/2, gx[-1]+res/2, gy[0]-res/2, gy[-1]+res/2]) / 1000
    fig, axes = plt.subplots(2, 3, figsize=(18, 12), sharex=True, sharey=True)
    axes = axes.ravel()
    transform = Transformer.from_crs(4326, crs['epsg'], always_xy=True)
    colors = ['#cf3c32', '#d17b00', '#238b45', '#8054a3', '#80552e']

    def overlay(ax, mask, color, alpha):
        from matplotlib.colors import to_rgb
        rgba = np.zeros((*mask.shape, 4))
        rgba[mask] = (*to_rgb(color), alpha)
        ax.imshow(rgba, extent=extent, origin='lower', interpolation='nearest')

    def marker(ax, site, color):
        x, y = transform.transform(site['longitude'], site['latitude'])
        ax.scatter(x/1000, y/1000, marker='o', s=90, c=[color], edgecolors='white', linewidths=1.5, zorder=5)
        ax.annotate(f"#{site['rank']}", (x/1000, y/1000), xytext=(8, 7), textcoords='offset points',
                    fontsize=10, weight='bold', bbox={'facecolor':'white','alpha':.9,'edgecolor':'none'}, zorder=6)

    for ax in axes:
        ax.imshow(d['dem'], extent=extent, origin='lower', cmap='Greys', alpha=.35)
        overlay(ax, water, '#2878cc', .6)
        ax.set_xlim(extent[:2])
        ax.set_ylim(extent[2:])
        ax.set_xlabel('Easting (km, UTM)')
        ax.set_ylabel('Northing (km, UTM)')
        ax.ticklabel_format(style='plain', useOffset=False)
        ax.locator_params(axis='both', nbins=4)

    axes[0].set_title('All candidate locations\nLocation markers only; no overlapping catchment fills', fontsize=11)
    for site, ax, color in zip(sites, axes[1:], colors):
        marker(axes[0], site, color)
        overlay(ax, site['_catchment_mask'], color, .28)
        geometry = site['catchment']['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        for polygon in polygons:
            for ring in polygon:
                lon, lat = np.asarray(ring).T
                x_edge, y_edge = transform.transform(lon, lat)
                ax.plot(np.asarray(x_edge)/1000, np.asarray(y_edge)/1000, color=color, linewidth=1.5)
        marker(ax, site, color)
        kind = site['collection_type'].replace('_', ' ')
        unfilled_area = site['unfilled_contributing_area_ha']
        unfilled_label = f'{unfilled_area:.4f}' if 0 < unfilled_area < .01 else f'{unfilled_area:.2f}'
        ax.set_title(f"#{site['rank']} · {kind}\n"
                     f"Connected: {site['catchment']['area_ha']:.2f} ha   |   Unfilled: {unfilled_label} ha",
                     fontsize=11, color=color)
        if site['catchment']['boundary_truncated']:
            ax.text(.02, .02, 'Catchment reaches data boundary', transform=ax.transAxes, fontsize=9,
                    bbox={'facecolor':'white','alpha':.9,'edgecolor':'none'})
    for ax in axes[len(sites)+1:]:
        ax.set_visible(False)
    fig.suptitle('Pond planning: compare each catchment separately', fontsize=18, y=.985)
    fig.text(.5, .015,
             'Every panel uses the same extent and scale. Coloured area = one connected catchment; blue = mapped-water exclusion buffer.\n'
             'Connected drainage assumes upstream depressions can spill. These alternatives can share drainage: do not add their areas.\n'
             'Markers are preliminary collection locations, not designed ponds. © OpenStreetMap contributors',
             ha='center', fontsize=10)
    for ax in axes[:3]:
        ax.set_xlabel('')
    fig.tight_layout(rect=(0,.08,1,.96), h_pad=3.0)
    output = sys.argv[2] if len(sys.argv)>2 else 'catchment_visualization.png'
    fig.savefig(output,dpi=150)
    plt.close(fig)
    print('Saved:',output)


if __name__ == '__main__':
    main()
