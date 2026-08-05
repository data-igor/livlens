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

## Branches — `dev` exists as a preview branch
- `dev` is a permanent branch, always kept mergeable, that PRs land on first
  so changes can be previewed live before they hit production.
- Preview `dev` via `raw.githack.com` — **but branch-name URLs
  (`.../dev/docs/index.html`) are cached hard by githack's upstream
  (statically.io) and can serve stale content for a long time even though the
  edge reports a cache MISS.** Always use the **commit SHA**, not the branch
  name, for a preview that's guaranteed fresh:
  `https://raw.githack.com/data-igor/livlens/<commit-sha>/docs/index.html`
  (get the SHA with `git rev-parse dev`). Query-string cache-busting does
  **not** work against this CDN.
- **`dev` is never merged into `main` automatically or as a matter of
  routine.** Promoting `dev` to production is a separate, explicit,
  human-approved step — same rule as any other merge to `main`.
- **PRs target `main`**, same as always, and are merged into `main` manually
  by a human — nothing automates that. `dev` is kept in sync separately and
  automatically: `.github/workflows/pr-to-main.yml` runs a build/syntax
  check on every PR targeting `main`, then does a plain `git merge` of that
  PR's branch straight into `dev` and pushes it (not a PR merge — the
  original PR is completely untouched and stays open against `main`). This
  gives a live `dev` preview of every open PR without ever using the PR
  system to land it, and without needing a human to remember to update
  `dev`.

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
It then optionally merges generated amenity columns from
`data/amenities/computed.json` (human CSV values still win on key collisions)
and writes `docs/areas.geojson`. The GitHub Action in
`.github/workflows/build.yml` runs this on every push that touches
`data/areas.csv` or `data/boundaries/` and commits the regenerated output. Do
not remove the rate-limit sleep or the User-Agent header on the Nominatim
path — both are required by its usage policy.

## Amenities pipeline (generated data only)
Amenity columns are **generated**, not hand-edited. Never add them to
`data/areas.csv`. The pipeline is:
1. `python3 scripts/amenities.py` — manual dev tool that fetches/caches
   Overpass data into `data/amenities/raw/*.json`, computes per-area columns,
   and writes `data/amenities/computed.json`.
2. `python3 scripts/build.py` — stdlib-only CI/runtime build step that reads
   `computed.json` if present and merges those generated columns into each
   feature's properties without overwriting any non-empty CSV value.

Dependency split: `scripts/amenities.py` may use Shapely because it is a
manually run generator whose committed JSON outputs make the build
reproducible/offline. `scripts/build.py` must stay stdlib-only so GitHub
Pages/CI never depends on third-party Python packages.

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

Note: prefer barrio-level granularity even for a whole town/municipality
outside Medellín (e.g. Envigado, Itagüí, La Estrella, Sabaneta) — map its
individual barrios as separate rows, the same way Medellín's comunas are
mapped, rather than unioning them into one municipality-wide polygon. A
single unioned outline hides useful "where to live" detail and reads as an
oversized "dummy" area on the map; it should only ever be a temporary
placeholder until barrio-level data is sourced (as was previously done for
Envigado, later replaced by its ~40 real barrios).

The whole Aburrá valley conurbation (Medellín + Envigado, Itagüí, La
Estrella, Sabaneta, and further out Bello/Caldas/Copacabana/Girardota/
Barbosa) is fair game to map at this same granularity — use each
municipality's own open-data GIS portal or, failing that, the Área
Metropolitana del Valle de Aburrá (AMVA) geoportal
(`https://datosabiertos.metropol.gov.co`, ArcGIS REST services under
`portalidem.metropol.gov.co`). The `geografico.metropol.gov.co:6080` ArcGIS
endpoint has been unreliable (times out) — prefer `portalidem.metropol.gov.co`
if both are available.

## Estimated columns (not live data)
Two pairs of columns in `data/areas.csv` are computed estimates, not
measurements — documented here so nobody mistakes them for live data or
wires up a paid API to "fix" them:
- `motorbike_min_to_poblado` / `motorbike_min_to_laureles` — straight-line
  (haversine) distance from each area's centroid to El Poblado's and
  Laureles' centroids, divided by an assumed average urban motorbike speed
  of 22 km/h. A live routing API (OSRM's public demo server) was tried
  first but rejected: it gave inconsistent, non-reproducible durations for
  the same two points on different requests (including a nonzero
  self-distance), which is worse than a transparent estimate. If a reliable
  keyless routing source is ever found, prefer real road-network duration
  over this straight-line estimate — but never ship something that fails
  the "self-distance must be 0" sanity check.
- `temp_min_c` / `temp_max_c` — derived from each area's centroid elevation
  (queried once from `api.open-elevation.com`) using Medellín's known
  elevation-based lapse rate (~0.6°C/100m) against a reference of Centro's
  climate (~1495m, avg daily min 15.5°C / max 28.5°C). Higher barrios in the
  hills run cooler on both ends; this is a documented approximation, not a
  weather API.

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
  filterable columns: numeric columns become a slider, columns with a short
  set of repeated values (≤8 distinct, excluding `notes` and the
  identity/geocoding columns) become checkbox filters.
Do not hardcode a fixed list of CSV columns in either — only `name`, `city`,
`country`, `status` are special-cased (shown as header/badge, not a generic
field/filter).

Numeric filter direction is also schema-driven, by suffix convention:
- `_nearest_m` (and other numeric columns without a special suffix) mean
  **lower is better** → slider acts as `≤`.
- `_min_c`, `_count_1km`, `_brands_1km`, `_per_km2` mean **higher is better**
  → slider acts as `≥`.
Keep future generated columns on that naming convention so `docs/app.js` keeps
working without hardcoded field names.

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

