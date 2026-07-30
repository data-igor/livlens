# AGENTS.md — LivLens

## What this project is
A static, single-purpose site: a CSV of city areas rendered as a colour-coded
map. No backend, no database, no build framework beyond one Python stdlib
script. Optimised for zero ongoing maintenance from the human owner.

## Hard constraints — do not violate
- **No API keys, no billing.** Map tiles and geocoding must stay free and
  keyless (currently: CARTO Voyager tiles + Nominatim). Do not switch to
  Google Maps or any paid/keyed service.
- **`data/areas.csv` is the only file the human is expected to edit.**
  Everything else (docs/areas.geojson, data/geo_cache.json) is generated.
  Never ask the user to hand-edit generated files.
- **Schema-agnostic frontend.** `docs/app.js` must render whatever columns
  exist in the CSV without code changes. Do not hardcode a fixed list of CSV
  columns in the panel renderer — only `name`, `city`, `country`, `status` are
  special-cased (they're shown as the header/badge, not as a generic field).
- **No new dependencies.** `scripts/build.py` uses only the Python standard
  library on purpose. `docs/` uses Leaflet via CDN only — no npm, no bundler.
- **Never commit to `main` directly.** Work on a branch, open a PR.

## Data contract (`data/areas.csv`)
Required columns: `name`, `city`, `country`, `status` (green|yellow|red).
Optional override columns: `lat`, `lon`, `radius_m` — these are a *fallback
only* (see the rulebook below); if `lat`/`lon` are present and no traced
boundary file exists for the area, `build.py` draws an approximate circle
instead. Every other column is free-form and rendered automatically in the
frontend side panel.

## Build pipeline
`scripts/build.py` reads the CSV and resolves each row's shape in this order:
1. `data/boundaries/<slug>.geojson` — a traced/official polygon (see rulebook below).
2. `lat`/`lon`/`radius_m` columns — draws an approximate circle (fallback only).
3. Nominatim geocoding (`polygon_geojson=1`, 1 req/sec, cached in
   `data/geo_cache.json`) — last resort, and prone to the administrative-vs-
   neighbourhood mismatch described in "Geocoding precision" below.
It then writes `docs/areas.geojson`. The GitHub Action in
`.github/workflows/build.yml` runs this on every push that touches
`data/areas.csv` or `data/boundaries/` and commits the regenerated output. Do
not remove the rate-limit sleep or the User-Agent header on the Nominatim
path — both are required by its usage policy.

## Defining an area's shape (the rulebook)
**A traced, real, street-bounded polygon is the only correct way to define an
area's shape.** Circles (`lat,lon,radius_m`) and raw Nominatim polygons are
both approximations and should be replaced as soon as a real boundary is
available — don't treat them as a finished state.

To add one:
1. Slugify the area's `name` (lowercase, spaces → hyphens, strip accents —
   see `slugify()` in `scripts/build.py`) to get the filename, e.g.
   `El Poblado` → `data/boundaries/el-poblado.geojson`.
2. Find a real polygon for it, in priority order:
   - The city/municipality's official open-data GIS portal (e.g. Medellín's
     "GeoMedellín" ArcGIS Open Data, or `datos.gov.co` for smaller
     municipalities like Envigado) — these publish barrio/neighbourhood
     boundaries as actual government-surveyed polygons, which is why this
     project prefers them over anything auto-geocoded.
   - OpenStreetMap via the Overpass API, if a `place=neighbourhood`/`suburb`
     *way or relation* (not just a point node) exists for that name.
   - As an absolute last resort, hand-trace a polygon around the streets
     colloquially understood to bound the area (e.g. with geojson.io) — but
     prefer an official source whenever one exists.
3. Save just the GeoJSON `geometry` object (a bare `Polygon` or
   `MultiPolygon`, not a full `Feature`/`FeatureCollection`) to that path.
4. Run `python3 scripts/build.py` — it should report `(traced boundary)` for
   that area, and never fall back to a circle for it again.

Note: for an entity that genuinely *is* a whole town (like `Envigado`, which
is its own municipality, not a Medellín barrio), the correct "real" boundary
is the whole municipal perimeter — a multi-km polygon is not a bug in that
case, just an accurate reflection of what the name refers to. When several
small official polygons make up the real area (e.g. Envigado's 39 barrios),
union them into one polygon (e.g. with `shapely.ops.unary_union`) rather than
picking just one.

## Testing changes
- `python3 scripts/build.py` — should report `(traced boundary)` for every
  area that has one, `(manual override (circle...))` for fallback rows, and
  `✓`/`⚠` for anything still relying on Nominatim. New rows may report `⚠` if
  Nominatim has no polygon; the fix is a real boundary file (preferred) or a
  `lat,lon,radius_m` override (temporary), not a code change.
- `node --check docs/app.js` — quick syntax check.
- `python3 -m http.server -d docs 8000` — serve locally and click around.

## Where things live
- `data/areas.csv` — user-edited source of truth
- `data/boundaries/*.geojson` — traced/official area shapes, keyed by slug (preferred over lat/lon/radius)
- `data/geo_cache.json` — generated, geocoding cache (fallback path only)
- `scripts/build.py` — CSV (+ boundaries) → GeoJSON
- `docs/` — the static site served by GitHub Pages (`index.html`, `app.js`,
  `style.css`, generated `areas.geojson`)
- `.github/workflows/build.yml` — CI that regenerates the GeoJSON on CSV/boundary changes

## Schema-agnostic frontend
Two things read whatever columns exist in the CSV without any code change:
- **Side panel** (`docs/app.js` `renderPanel`) — renders every populated
  property on the clicked feature.
- **Filters panel** (`docs/app.js` `buildFilterDefinitions`) — auto-detects
  filterable columns: numeric columns become a max-value slider, columns with
  a short set of repeated values (≤8 distinct, excluding `notes` and the
  identity/geocoding columns) become checkbox filters.
Do not hardcode a fixed list of CSV columns in either — only `name`, `city`,
`country`, `status` are special-cased (shown as header/badge, not a generic
field/filter).

## Filter → colour model
Every filter is marked **strict** or **negotiable** (default). An area's
computed colour: red if it fails an active strict filter (dealbreaker),
yellow if it fails only negotiable filters, green if it passes everything
active. If no filter is active, fall back to the manual `status` CSV column.
Do not remove this fallback — it's what makes the map useful with zero
filters touched.

## Geocoding precision
Nominatim often resolves a Medellín barrio name to the enclosing "Comuna N"
administrative boundary (much larger than the colloquial neighbourhood people
mean) rather than the neighbourhood itself — this happened silently for
El Poblado, La Candelaria, and Belén in the seed data. `build.py`'s
`bbox_diagonal_m` + `SUSPICIOUSLY_LARGE_M` check flags (but doesn't block)
polygon matches over ~4km across. When you see that warning, the fix is a
`lat,lon,radius_m` override in the CSV, not a code change — get better
neighbourhood-level coordinates from Nominatim's `neighbourhood`/`place`
class results rather than its `administrative`/`boundary` class results.

