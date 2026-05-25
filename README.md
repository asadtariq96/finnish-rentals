# finnish-rentals

Reverse-engineered API clients for the two big Finnish rental sites
([vuokraovi.com](https://vuokraovi.com) + [asunnot.oikotie.fi](https://asunnot.oikotie.fi)),
plus a unified CLI that searches both, filters listings by free-text features
(parking garage + EV charging, by default), and dedupes the same building across
both sites.

Stdlib-only Python (no pip install needed). Reverse-engineering was done with
[browser-harness](https://github.com/browser-use/browser-harness) and LLM
verification of the regex against ~470 real Espoo listings.

```bash
python3 rentals.py matches                     # default: ≤€1350, ≥60m², 3 rooms, parking+EV
python3 rentals.py matches --rent-max 1500 --rooms 3,4
python3 rentals.py matches --no-ev --json > out.json
```

## Layout

```
finnish_rental.py   shared Finnish-text regexes (parking garage, EV charging)
vuokraovi.py        client + helpers for vuokraovi.com
oikotie.py          client + helpers for asunnot.oikotie.fi (auto-refreshes tokens)
rentals.py          unified CLI — searches both, filters, dedupes by building
data/               point-in-time snapshots from 2026-05-25 (full text + LLM verdicts)
```

## CLI

### `matches` — the main command

Searches both sites, applies parking/EV filter, dedupes, sorts by rent.

```bash
python3 rentals.py matches                              # both sites, defaults
python3 rentals.py matches --source oikotie             # one site
python3 rentals.py matches --rent-max 1500 --size-min 50 --rooms 2,3
python3 rentals.py matches --no-ev                      # parking only
python3 rentals.py matches --no-parking --no-ev         # all listings, no feature filter
python3 rentals.py matches --json                       # JSON instead of table
```

Output columns: `source | €/mo | m² | rooms | district | address | also-on (other site) | url`.

The dedup key is `(street_lower, house_number, stairwell_letter)`. When the same
building shows up on both sites, the cheaper rent wins and `also-on` notes the
other site.

### `search` — quick first-page peek

Skips detail fetches and the feature filter; just shows what each site has on
the first page for the given filters.

```bash
python3 rentals.py search --rent-max 1500 --size-min 60 --rooms 3
```

### `detail` — inspect one listing

```bash
python3 rentals.py detail vuokraovi:fk3684
python3 rentals.py detail oikotie:24502080
```

Prints the parking/EV flags and the full Finnish description.

### Python API (importable)

```python
import vuokraovi, oikotie

# Vuokraovi
for card in vuokraovi.search_all(rent_max=1350, size_min=60,
                                  room_counts=["THREE_ROOMS"]):
    d = vuokraovi.detail(card["friendlyId"])

# Oikotie
for card in oikotie.search_all(price_max=1350, size_min=60, roomCount=[3],
                                buildingType=[1,2,4,8,32,64,128,256,512]):
    d = oikotie.detail(card["cardId"])

# Shared filter (works on either site's description text)
from finnish_rental import has_parking_garage, has_ev_charging
has_parking_garage("Talon pysäköintipaikat ovat pysäköintihallissa.")  # True
has_ev_charging("sähköauton latauspaikka 70 €/kk")                     # True
```

## How the APIs work

### vuokraovi.com — open, two custom headers

```http
POST https://api.vuokraovi.com/distant/swordsman/v3/announcements/rental/search/listpage
GET  https://api.vuokraovi.com/distant/swordsman/v3/announcement/rental/details?friendlyId=<id>

Headers required on every call (no auth token):
  x-portal-identifier: VUOKRAOVI
  x-uid: <any uuid>
```

Pagination via `pagination.firstResult` + `pagination.maxResults` in the POST body.

### asunnot.oikotie.fi — three rotating headers

```http
GET https://asunnot.oikotie.fi/api/search?...&offset=N&limit=500
GET https://asunnot.oikotie.fi/api/card/<cardId>

Headers (read from <meta> tags on any oikotie.fi page):
  OTA-token   (meta name="api-token")
  OTA-loaded  (meta name="loaded")
  OTA-cuid    (meta name="cuid")
```

Tokens rotate. `oikotie.py` auto-refreshes them on `401` by re-scraping
`/vuokra-asunnot`.

> ⚠️ The web URL uses `pagination=N`, but the **API ignores that param** and uses
> `offset=N` instead. `limit=500` pulls all results in one call.

## The free-text feature filter

Structured fields on both sites (`carParkingFeatures`, `housingCompanyFeatures`)
are almost always empty for these things. Parking-garage and EV-charging info
lives in the free-text Finnish description.

`finnish_rental.py` has two regexes, verified against 467 real listings via
8 independent LLM reviews (zero false positives, zero false negatives at
the end):

- **`has_parking_garage(text)`** — `pysäköintihalli`, `pysäköintilaitos`,
  `pysäköintirakennus`, `pysäköintitalo`, `paikoitushalli`, `autohalli`,
  `autotalli`/`autotalleja`, `parkkihalli`, `parkkitalo`, `pihakannen alla`,
  `maanalainen pysäköinti`, English `parking hall/garage`, and English
  `parking space ... stairwell/elevator/underground/garage/indoor`.

- **`has_ev_charging(text)`** — `sähköauton lataaminen/latauspiste/latauspaikka/lataustolppa`,
  `latauslaite`, `latausmahdollisuus`, `N kW latauksella`,
  `EV charging`, `electric vehicle ... charging`.

### Finnish-text gotchas the LLM caught

1. **Noun-stem changes.** `pysäköintilaitos` → `pysäköintilaito**ks**essa`
   (inessive). The regex anchors on the stem `pysäköintilaito`, not the
   nominative.
2. **Verb forms differ from noun forms.** `lataus` (noun) starts `latau-`,
   but `lataaminen` (gerund) starts `lataa-`. Pattern is `lata[au]`.
3. **Context-only EV signals.** `11 kW latauksella` is unambiguously EV
   charging even without `sähköauto` mentioned. Separate `\d+\s*kW\s*lata`
   clause covers this.
4. **Block heater ≠ EV charging.** `lämmityssähkö` / `lämpötolppa` /
   `sähkötolppa` / `lämmityspistoke` are engine-block heater outlets
   common at Finnish outdoor parking spots — explicitly excluded.

## Data snapshots (`data/`)

Captured 2026-05-25 from Espoo searches with default filters
(rent≤€1350, size≥60m², 3 rooms).

- `vuokraovi_texts.json` — 80 listings, raw text + regex verdicts
- `oikotie_texts.json` — 387 listings, raw text + regex verdicts
- `*_chunk_N.json` — same texts split into chunks for parallel LLM review
- `*_llm_N.json` — per-chunk LLM verdicts with evidence quotes
- `*_matches.json` / `*_verdict.json` — final filtered lists / unified verdicts

Useful for diffing future scrapes against today's data, or for unit-testing
regex changes against a frozen ground truth.

## TODO / future improvements

Ideas worth picking up next, roughly in order of payoff per effort:

### Easy wins
- [ ] **Make `rentals` a real CLI command.** Add `pyproject.toml` with a `[project.scripts] rentals = "rentals:main"` entry; `pipx install .` makes `rentals matches` work from anywhere.
- [ ] **`--year-min` filter.** Vuokraovi search already supports `yearMin` in the payload; expose it in the CLI. (Removing it is why the unified search went from 80 → 253 listings on Vuokraovi.)
- [ ] **`--city` flag.** Both APIs accept other Finnish cities (Helsinki, Tampere, Vantaa, Turku). Right now `Espoo` (`FI_UUSIMAA_ESPOO`, oikotie `[39,6,"Espoo"]`) is hardcoded. Build a small lookup table.
- [ ] **CSV export.** `--csv` next to `--json` for spreadsheet workflows.
- [ ] **Sort by €/m².** Right now sort is by rent ascending only; price-per-sqm is more honest for comparing different sizes.
- [ ] **Drop the data scattered in `~/Developer/`** — the canonical copies live in `data/` now.

### Medium
- [ ] **Add etuovi.com.** Third major site, very similar shape. Same playbook applies (open browser-harness, capture XHR, find auth in `<meta>` or JS bundle).
- [ ] **Smarter dedup across stairwells.** Right now `(street, house, stair)` keeps `Karakalliontie 8 A` distinct from `Karakalliontie 8 D`. Sometimes Vuokraovi omits the stairwell letter — those slip through dedup. Optionally collapse by `(street, house)` with a `--collapse-stairwells` flag, showing rent range.
- [ ] **Watch mode.** `rentals watch` polls every N minutes; diffs against last snapshot; alerts (Slack/email/desktop notification) when a new listing matches the filter. Use the existing snapshot format in `data/` as the diff baseline.
- [ ] **More feature filters.** The free-text Finnish corpus is rich — easy extensions for the same regex-then-LLM pattern: sauna in unit (`oma sauna`), dishwasher (`astianpesukone`), balcony/loggia (`parveke`, `terassi`), pet-friendly (`lemmikit sallittu`).
- [ ] **Regression test for the regex.** `data/*_llm_*.json` is ground-truth — wire it into a `pytest` that re-runs `has_parking_garage`/`has_ev_charging` on every snapshot text and fails if a verdict flips. Stops future regex changes from silently regressing.
- [ ] **Pull listing photos.** Both detail APIs include image URLs; download the top 3 per shortlisted unit for offline browsing.

### Bigger
- [ ] **Geocoding-aware filter.** Both APIs return `latitude/longitude`. Filter by distance to a point (workplace, daycare, metro stop). Use the `Haversine` formula; no dep needed.
- [ ] **HTML report.** Auto-generate a single-file HTML page with the shortlisted units, sorted, with photos and a map (Leaflet + OSM tiles). Open in browser after each run.
- [ ] **Token-rotation robustness for oikotie.** Right now the refresh on 401 scrapes `/vuokra-asunnot` HTML to re-read meta tags. If oikotie ever rate-limits the HTML page or moves the meta, this breaks. Worth wrapping in a clearer error and considering a backoff.
- [ ] **English-text handling.** Oikotie listings sometimes have English-only descriptions (e.g. `23692867`). The current regex covers a couple of phrases (`parking space ... elevator`) but the bulk is Finnish-only. If English listings become common, broaden the EV/parking patterns symmetrically.
- [ ] **Saved-search profiles.** A `~/.finnish-rentals.toml` with named search profiles (`work`, `family`, `cheap`) so `rentals matches --profile work` replays a saved filter set.

### Maintenance gotchas to remember
- Vuokraovi's API has been stable; oikotie's `OTA-token` rotates per session — auto-refresh handles it but watch for layout changes on the landing page.
- Oikotie's API silently ignores the `pagination=N` param (that's only for URL routing on the web app); always use `offset=N`.
- Finnish noun inflection (`-os` → `-okse-`, `-us` → `-ukse-`) is the main source of regex bugs. Always anchor on stems, never on nominatives.

## License

MIT — do whatever; no warranty. Personal scraper for personal use; mind the
rate limit (the scrapers sleep ~0.2s between detail requests by default).
