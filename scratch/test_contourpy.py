import numpy as np
import contourpy

mask = np.zeros((10, 10), dtype=bool)
mask[3:7, 3:7] = True
mask[4, 7] = True # A small extension

# Convert to float for contouring
mask_float = mask.astype(float)

# We want the boundary between 0 and 1, so level 0.5
c = contourpy.contour_generator(z=mask_float)
lines = c.lines(0.5)

print(f"Found {len(lines)} polygons")
for i, line in enumerate(lines):
    print(f"Polygon {i}: shape {line.shape}")
    print(line)
