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
Optional override columns: `lat`, `lon`, `radius_m` — if `lat`/`lon` are
present, `build.py` skips geocoding and draws a circle instead. Every other
column is free-form and rendered automatically in the frontend side panel.

## Build pipeline
`scripts/build.py` reads the CSV, geocodes new rows via Nominatim
(`polygon_geojson=1`, 1 req/sec, cached in `data/geo_cache.json`), and writes
`docs/areas.geojson`. The GitHub Action in `.github/workflows/build.yml` runs
this on every push that touches `data/areas.csv` and commits the regenerated
output. Do not remove the rate-limit sleep or the User-Agent header — both are
required by Nominatim's usage policy.

## Testing changes
- `python3 scripts/build.py` — should report `✓` for every existing row.
  New rows may report `⚠` if Nominatim has no polygon; that's expected, not
  a bug — the fix is a `lat,lon,radius_m` override in the CSV, not a code change.
- `node --check docs/app.js` — quick syntax check.
- `python3 -m http.server -d docs 8000` — serve locally and click around.

## Where things live
- `data/areas.csv` — user-edited source of truth
- `data/geo_cache.json` — generated, geocoding cache
- `scripts/build.py` — CSV → GeoJSON
- `docs/` — the static site served by GitHub Pages (`index.html`, `app.js`,
  `style.css`, generated `areas.geojson`)
- `.github/workflows/build.yml` — CI that regenerates the GeoJSON on CSV changes
