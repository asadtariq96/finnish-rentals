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

## License

MIT — do whatever; no warranty. Personal scraper for personal use; mind the
rate limit (the scrapers sleep ~0.2s between detail requests by default).
