# LivLens

See the city before you sign the lease.

LivLens is a tiny map tool for deciding where to live. You keep a plain CSV of
areas — with a status of `green`, `yellow`, or `red` — and the site draws them
on a map, colour-coded, click-to-see-details.

**Live site:** https://data-igor.github.io/livlens

## How to add an area

1. Open [`data/areas.csv`](data/areas.csv) on GitHub and click the pencil (edit) icon.
2. Add a new row: at minimum `name`, `city`, `country`, and `status` (`green` /
   `yellow` / `red`). Add anything else you want to remember about the place —
   rent, safety, noise, vibe, notes — any column you add shows up automatically
   on the site, and short/numeric columns become filters automatically too
   (see below).
3. **Give it a real shape.** The map draws exactly the streets that bound the
   area — not an approximated circle — using a hand-picked/official polygon
   file in `data/boundaries/<slug>.geojson` (slug = the area's `name`,
   lowercased, spaces → hyphens, e.g. "La Candelaria" → `la-candelaria.geojson`).
   This is the **only** correct way to define an area's shape; see
   [`AGENTS.md`](AGENTS.md#defining-an-areas-shape-the-rulebook) for exactly
   where to find these boundaries (city open-data portals, Overpass, etc.) and
   how to add one. Until a real boundary file exists, `scripts/build.py` falls
   back to `lat`, `lon`, `radius_m` columns and draws a rough circle instead —
   that's a temporary placeholder, not the end state.
4. Commit the change (to a new branch + PR, or directly if you're comfortable).
   A GitHub Action rebuilds the map automatically — you don't need to run
   anything locally.

## Filters

Click **Filters** on the site to open the filter panel. It's built
automatically from whatever columns exist in the CSV: numeric columns (like
`rent_usd`) become a "maximum acceptable" slider; short repeated-value columns
(like `safety`, `noise`, `verdict`) become checkboxes.

Each filter has a **Strict** toggle:
- **Strict** — a dealbreaker. Any area failing this filter turns **red**.
- **Negotiable** (default) — a soft mismatch. Turns the area **yellow**
  instead of red.
- An area that passes every active filter is **green**.
- Touch nothing, and the map falls back to each area's manual `status` column.

## Local development

```
python3 scripts/build.py      # CSV -> docs/areas.geojson
python3 -m http.server -d docs 8000
```

Then open http://localhost:8000.

## How it works

- `data/areas.csv` — the only file you're expected to edit.
- `scripts/build.py` — reads the CSV, geocodes each area against
  [Nominatim](https://nominatim.org/) (OpenStreetMap, free, no API key), and
  writes `docs/areas.geojson`. Results are cached in `data/geo_cache.json` so
  areas are only geocoded once.
- `docs/` — a static site (Leaflet + vanilla JS) served by GitHub Pages.
- `.github/workflows/build.yml` — runs the build script whenever
  `data/areas.csv` changes, and commits the regenerated GeoJSON.

No backend, no database, no API keys, no billing.

## License

MIT — see [LICENSE](LICENSE).
