# Vuokraovi live-search web app

Frontend + backend around vuokraovi.com's official search API, with a **hybrid**
data model:

- The backend **eagerly prefetches and caches the entire municipality** in the
  background (memory + disk), so most filtering/searching is **instant and fully
  client-side — no backend round-trips**.
- **Free-text** keyword search is the only live call: it hits the upstream
  full-text search through the backend (debounced).

## Run

```bash
./app/run.sh            # http://localhost:8933
PORT=9000 ./app/run.sh  # override port
```

First launch needs the venv (once):

```bash
python3 -m venv appenv && ./appenv/bin/pip install flask
```

## Hybrid model

1. Pick a municipality → backend serves the **cached full dataset** (or kicks off
   a background prefetch and the UI shows progress until it's warm). First warm of
   Espoo (1,883 listings) took ~12 s of background paging; afterwards it loads
   from cache instantly, and survives restarts via `app/cache/*.json`.
2. All structural filters — rent, €/m², area, rooms, **building type**, year built,
   floor / building height, **district**, availability, landlord, new-build, and
   toggles for **has video / has upcoming viewing / private landlord** — run
   **client-side on the full set** (instant, zero backend). The table paginates
   client-side (100/page) and the map uses leaflet marker clustering so ~2,000
   markers stay smooth.
3. Typing in **Free text** hits live upstream full-text search (debounced 450 ms);
   clearing it reverts to the full cached municipality.
4. Cache TTL is 15 min (env `VUOKRAOVI_CACHE_TTL`, seconds); a stale snapshot is
   served immediately while a background refresh runs. "Refresh municipality"
   re-pulls on demand.

## Layout

- `app/app.py`                     — Flask backend (proxy + prefetch cache)
- `app/static/explorer.html`      — the SPA (map + divider + sortable table)
- `app/test_app.py`               — backend unit tests (mocked upstream & worker)
- `app/cache/*.json`              — persisted full-municipality snapshots
- `app_municipalities.json`       — 11 validated FI municipality codes

## API surface

| Route | Purpose |
|-------|---------|
| `GET /`                                | serves the SPA |
| `GET /api/municipalities`              | 11 Finnish municipalities |
| `GET /api/municipality/<code>`         | full cached dataset (starts bg prefetch if missing/stale) |
| `GET /api/municipality/<code>/status`  | prefetch progress `{prefetching, fetched, count}` |
| `POST /api/search`                     | live search (used for free-text; also warms prefetch on municipality-only) |
| `GET /api/districts?code=…`            | districts of a municipality |
| `GET /api/details/<friendlyId>`        | full ad detail / description |
| `GET /api/parking`                     | user-owned parking records (43 buildings; merged into announcements too) |

## Parking data (user-owned, not from vuokraovi)

Building-level parking facts you verify yourself (autohalli, underground,
heating, EV charging, engine-heater plugs) live in
`export/espoo_buildings.csv` — one row per building, columns
`parking_hall_exists … evidence_note`. The backend loads it lazily and
**re-reads it whenever the file's mtime changes** (edit the CSV, refresh the
page — no restart needed; path overridable via env `VUOKRAOVI_PARKING_CSV`).

- Join key: building identity = `(lat, lng)` rounded to 5 dp, the same key
  used to dedupe listings into buildings — so every ad of a verified building
  inherits its parking record (`GET /api/municipality/<code>` and
  `/api/search` both attach `parking` per announcement; raw list is at
  `GET /api/parking`).
- Frontend: **Parking** filter group (has hall / underground / heated /
  EV / plug) + a **Parking** column of badges + a parking line in map popups.
- Only rows with a non-empty `parking_hall_exists` are attached; everything
  else renders `–` (no data) and never matches a parking filter.

## Errors / notes

- Upstream only accepts `maxResults` ≤ 30, so the backend pages internally.
- `newBuildingSearchCriteria` only accepts `ALL_PROPERTIES` upstream → new-builds
  is a client-side filter (the field is per-listing `newBuilding`).
- Prefetched data is structured list fields only; full ad descriptions are NOT
  bulk-cached (that would be ~1 extra call per listing). Keyword-over-description
  is why free-text runs live.

## Tests

```bash
cd app && ../appenv/bin/python -m unittest -v test_app
```
