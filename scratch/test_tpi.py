import numpy as np
from scipy.ndimage import uniform_filter

dem = np.array([
    [10, 10, 10, 10, 10],
    [10,  8,  5,  8, 10],
    [10,  5,  2,  5, 10],
    [10,  8,  5,  8, 10],
    [10, 10, 10, 10, 10],
], dtype=float)

# Mean filter 3x3
mean_dem = uniform_filter(dem, size=3, mode="reflect")
tpi = dem - mean_dem

print("DEM:\n", dem)
print("Mean DEM:\n", mean_dem)
print("TPI:\n", tpi)

tpi_capped = np.minimum(tpi, 0.0)
tpi_min = tpi_capped.min()
if tpi_min < 0:
    depr_norm = 1.0 - (tpi_capped / tpi_min)
else:
    depr_norm = np.ones_like(dem)
    
print("Depression Norm:\n", np.round(depr_norm, 2))
