#!/usr/bin/env python3
"""
build.py — turns data/areas.csv into docs/areas.geojson.

Reads each row, geocodes it against OpenStreetMap Nominatim (free, no API
key), and writes a GeoJSON FeatureCollection that the frontend (docs/app.js)
loads directly. Every CSV column is copied into the GeoJSON `properties`, so
adding a column to the CSV never requires a code change here or in the
frontend.

Usage:
    python3 scripts/build.py

Requires only the Python standard library.
"""
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "areas.csv"
CACHE_PATH = ROOT / "data" / "geo_cache.json"
OUT_PATH = ROOT / "docs" / "areas.geojson"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "LivLens/1.0 (personal project; https://github.com/data-igor/livlens)"
DEFAULT_RADIUS_M = 700
RATE_LIMIT_SECONDS = 1.0  # Nominatim usage policy: max 1 request/second


def load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def cache_key(name, city, country):
    return f"{name}|{city}|{country}".lower()


def geocode(name, city, country):
    """Query Nominatim for a polygon boundary. Returns (geojson_geometry, display_name) or (None, None)."""
    query = f"{name}, {city}, {country}"
    params = {
        "q": query,
        "format": "json",
        "polygon_geojson": 1,
        "limit": 1,
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            results = json.loads(resp.read().decode())
    except Exception as exc:  # network / HTTP errors — treat as a miss
        print(f"  ! Nominatim request failed for '{query}': {exc}")
        return None, None

    if not results:
        return None, None

    result = results[0]
    geometry = result.get("geojson")
    display_name = result.get("display_name")
    if not geometry or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        # Only accept real area boundaries here; points fall through to the
        # lat/lon-or-circle handling in main().
        return None, display_name
    return geometry, display_name


def circle_polygon(lat, lon, radius_m, points=48):
    """Approximate a circle of radius_m metres around (lat, lon) as a GeoJSON Polygon."""
    import math

    coords = []
    lat_rad = math.radians(lat)
    for i in range(points + 1):
        angle = 2 * math.pi * i / points
        d_lat = (radius_m * math.cos(angle)) / 111320.0
        d_lon = (radius_m * math.sin(angle)) / (111320.0 * math.cos(lat_rad) or 1e-9)
        coords.append([lon + d_lon, lat + d_lat])
    return {"type": "Polygon", "coordinates": [coords]}


def main():
    if not CSV_PATH.exists():
        print(f"No CSV found at {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    cache = load_cache()
    features = []
    misses = []

    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        name = (row.get("name") or "").strip()
        city = (row.get("city") or "").strip()
        country = (row.get("country") or "").strip()
        if not name:
            continue

        lat_override = (row.get("lat") or "").strip()
        lon_override = (row.get("lon") or "").strip()
        radius_str = (row.get("radius_m") or "").strip()

        geometry = None
        source = None

        if lat_override and lon_override:
            lat, lon = float(lat_override), float(lon_override)
            radius = float(radius_str) if radius_str else DEFAULT_RADIUS_M
            geometry = circle_polygon(lat, lon, radius)
            source = "manual override"
        else:
            key = cache_key(name, city, country)
            if key in cache:
                geometry = cache[key]["geometry"]
                source = "cache"
            else:
                geometry, display_name = geocode(name, city, country)
                time.sleep(RATE_LIMIT_SECONDS)
                if geometry:
                    cache[key] = {"geometry": geometry, "display_name": display_name}
                    source = "nominatim (polygon)"
                elif display_name:
                    # Got a point match but no polygon — not enough to draw
                    # a sensible shape. Report it so the user can add lat/lon.
                    source = None
                    misses.append((name, f"only a point match ({display_name}) — add lat,lon,radius_m"))
                else:
                    misses.append((name, "no match — add lat,lon,radius_m"))

        if geometry:
            properties = {k: v for k, v in row.items() if v not in (None, "")}
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties,
                }
            )
            print(f"  \u2713 {name} ({source})")
        elif source is None and name not in [m[0] for m in misses]:
            pass  # already recorded in misses above

    save_cache(cache)

    geojson = {"type": "FeatureCollection", "features": features}
    OUT_PATH.write_text(json.dumps(geojson, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(features)} area(s) to {OUT_PATH}")

    if misses:
        print("\nAreas that could not be geocoded:")
        for name, reason in misses:
            print(f"  \u26a0 {name}: {reason}")


if __name__ == "__main__":
    main()
