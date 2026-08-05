# LivLens

See the city before you sign the lease.

LivLens is a tiny map tool for deciding where to live. Areas live in a plain
CSV — a status of `green`, `yellow`, or `red`, plus whatever else you care
about (rent, noise, vibe) — and the site draws them on a map, colour-coded,
click-to-see-details. There's also a set of optional data layers (cafés,
gyms, supermarkets, metro stations, noise score...) pulled for free from
OpenStreetMap.

**Live site:** https://data-igor.github.io/livlens

## Add an area

1. Open [`data/areas.csv`](data/areas.csv) on GitHub and click the pencil
   (edit) icon.
2. Add a row: at minimum `name`, `city`, `country`, `status`. Add any other
   column you want to track — it shows up on the site automatically, and
   becomes a filter too if it's numeric or short/categorical.
3. Give it a real shape: drop a boundary polygon at
   `data/boundaries/<slug>.geojson` (slug = the name, lowercased, spaces to
   hyphens). Without one, the map falls back to a rough circle from
   `lat`/`lon`/`radius_m`. See [`AGENTS.md`](AGENTS.md) for where to find
   boundary files.
4. Commit (branch + PR — never straight to `main`). A GitHub Action rebuilds
   the map for you.

## Filters

Click **Filters** to open the panel — it's built automatically from whatever
columns exist in the CSV. Each filter has a **Strict** toggle: strict means
a dealbreaker (fails → red), negotiable means a soft mismatch (fails →
yellow). Pass everything active → green. Touch nothing and an area just
shows its manual `status`.

## Contributing a new feature or dataset

This is a weekend project, kept deliberately small — no backend, no database,
no API keys. If you want to add something:

- **A new CSV column** — just add it, nothing else to do (see "Add an area"
  above).
- **A new OSM-derived layer or score** (like the noise score, or the
  café/gym/supermarket layers) — add it in `scripts/amenities.py`, run it
  locally, commit the regenerated `data/amenities/*.json`. It'll show up in
  the frontend without touching `docs/app.js`, as long as it follows the
  existing column-naming conventions.
- **UI changes** — `docs/` is plain Leaflet + vanilla JS, no build step.
  Edit, refresh, done.

Read [`AGENTS.md`](AGENTS.md) first — it has the actual rules (why no paid
APIs, how the build pipeline works, the noise-score formula, etc.) and is
kept short on purpose.

## Local development

```
python3 scripts/build.py      # CSV -> docs/areas.geojson
python3 -m http.server -d docs 8000
```

Then open http://localhost:8000.

## License

MIT — see [LICENSE](LICENSE).
