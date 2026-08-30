import numpy as np
import contourpy

mask = np.zeros((10, 10), dtype=bool)
mask[2:4, 6:9] = True  # Rows 2..3 (y), Cols 6..8 (x)

c = contourpy.contour_generator(z=mask.astype(float))
lines = c.lines(0.5)

print("Rows 2..3, Cols 6..8")
print(lines[0])
