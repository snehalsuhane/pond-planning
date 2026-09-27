import json
import time
from app import create_app

client = create_app().test_client()

# Polygon 1
poly1 = {
    "type": "Polygon",
    "coordinates": [[
        [81.286321, 21.263539],
        [81.286321, 21.264539],
        [81.287321, 21.264539],
        [81.287321, 21.263539],
        [81.286321, 21.263539]
    ]]
}

print("First try...")
res1 = client.post('/api/analyzeArea', json={'land_area': poly1})
print("Res1 status:", res1.status_code)
if res1.status_code != 200:
    print(res1.get_json())

# Polygon 2 (slightly different location)
poly2 = {
    "type": "Polygon",
    "coordinates": [[
        [81.386321, 21.363539],
        [81.386321, 21.364539],
        [81.387321, 21.364539],
        [81.387321, 21.363539],
        [81.386321, 21.363539]
    ]]
}

print("\nSecond try...")
res2 = client.post('/api/analyzeArea', json={'land_area': poly2})
print("Res2 status:", res2.status_code)
if res2.status_code != 200:
    print(res2.get_json())

