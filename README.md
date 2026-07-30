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
3. Commit the change (to a new branch + PR, or directly if you're comfortable).
   A GitHub Action geocodes the new area and rebuilds the map automatically —
   you don't need to run anything locally.

If the area can't be found automatically (small barrios sometimes aren't in
OpenStreetMap, or Nominatim only has the wider comuna/district boundary), fill
in the `lat`, `lon`, and optionally `radius_m` columns yourself and it'll draw
a circle there instead. `scripts/build.py` warns in its output if a matched
polygon looks suspiciously large (>4km across) — that's usually a sign it
grabbed an administrative district rather than the neighbourhood.

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
