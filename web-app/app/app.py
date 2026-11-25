#!/usr/bin/env python3
"""Vuokraovi live-search backend.

Proxies api.vuokraovi.com so the frontend can run real searches against
the official API (no embedded/static data). Serves the SPA from static/.
Run:  ./appenv/bin/python app/app.py   (default port 8933)
"""
import csv, json, os, time, urllib.parse, urllib.request, urllib.error

from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://api.vuokraovi.com/distant/swordsman"
# anonymous client id from the real site session
XUID = "dea18320-28fa-42c0-b6b8-1129ec34552c"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.vuokraovi.com",
    "Referer": "https://www.vuokraovi.com/",
    "X-Portal-Identifier": "VUOKRAOVI",
    "X-Uid": XUID,
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
}
PAGE = 30          # vuokraovi caps maxResults at 30
DEFAULT_MAX = 200  # backend gathers up to this many results by paging

with open(os.path.join(BASE_DIR, "..", "app_municipalities.json")) as f:
    MUNICIPALITIES = json.load(f)

# ---- user-owned parking data (NOT from vuokraovi) ----
# Merged into every announcement by building identity: (lat,lng) rounded to
# 5 dp — the same key used to dedupe listings into buildings in the CSV.
# Re-reads the file lazily when its mtime changes, so editing the CSV and
# refreshing the page picks up new rows without a backend restart.
PARKING_CSV = os.environ.get(
    "VUOKRAOVI_PARKING_CSV",
    os.path.join(BASE_DIR, "..", "export", "espoo_buildings.csv"),
)
PARKING_FIELDS = [
    "parking_hall_exists", "hall_underground", "hall_type", "covered",
    "heated_status", "engine_heater_plug", "elevator_to_hall", "ev_charging",
    "parking_operator", "verification_status", "evidence_note",
]
_parking = {}        # (lat, lng) -> record
_parking_mtime = 0.0


def _load_parking():
    global _parking, _parking_mtime
    try:
        m = os.stat(PARKING_CSV).st_mtime
    except OSError:
        _parking, _parking_mtime = {}, 0.0
        return _parking
    if m == _parking_mtime and _parking:
        return _parking
    out = {}
    try:
        with open(PARKING_CSV) as f:
            for r in csv.DictReader(f):
                lat_s = (r.get("latitude") or "").strip()
                lng_s = (r.get("longitude") or "").strip()
                if not lat_s or not lng_s:
                    continue
                try:
                    lat = round(float(lat_s), 5)
                    lng = round(float(lng_s), 5)
                except ValueError:
                    continue
                rec = {k: (r.get(k) or "").strip() for k in PARKING_FIELDS}
                if rec.get("parking_hall_exists"):          # only verified rows
                    out[(lat, lng)] = rec
    except OSError:
        pass
    _parking, _parking_mtime = out, m
    return _parking


def _attach_parking(announcements):
    """Stamp each announcement with its building's parking record (or leave unset)."""
    pk = _load_parking()
    if not pk:
        return announcements
    for a in announcements:
        if not isinstance(a, dict):
            continue
        lat, lng = a.get("latitude"), a.get("longitude")
        if lat is None or lng is None:
            continue
        rec = pk.get((round(lat, 5), round(lng, 5)))
        if rec:
            a["parking"] = rec
    return announcements

app = Flask(__name__, static_folder=None)

# ---- full-municipality prefetch cache ----
# Structured list fields only (address/district/rent/area/rooms/year/floor/
# avail/landlord/lat-lng). Full descriptions stay live via /api/search text.
import threading as _threading

CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE = {}             # code -> {"fetchedAt": float, "count": int, "announcements": [...]}
PREFETCHING = {}       # code -> True while a background fetch is running
CACHE_LOCK = _threading.Lock()
_CACHE_TTL = int(os.environ.get("VUOKRAOVI_CACHE_TTL", 900))   # seconds

_MUNI_BY_CODE = {m["code"]: m["name"] for m in MUNICIPALITIES}


def _save_cache(code, data):
    with open(os.path.join(CACHE_DIR, code + ".json"), "w") as f:
        json.dump(data, f)


def _load_disk_cache(code):
    p = os.path.join(CACHE_DIR, code + ".json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _fetch_all_pages(code, short_name):
    """Page the whole municipality (hard cap 30/page) until all results collected."""
    body = _build_body({"location": {"code": code, "shortName": short_name, "districts": []}})
    out, first, count = [], 0, None
    while True:
        body["pagination"]["firstResult"] = first
        body["pagination"]["page"] = (first // PAGE) + 1
        st, data = _proxy("POST", "/v3/announcements/rental/search/listpage", body=body)
        if st != 200:
            break
        count = data.get("countOfAllResults", count)
        batch = data.get("announcements", [])
        out.extend(batch)
        if not batch or len(batch) < PAGE or len(out) >= (count or len(out)):
            break
        first += PAGE
        time.sleep(0.2)
    return count, out


def _prefetch_worker(code, short_name):
    try:
        count, out = _fetch_all_pages(code, short_name)
        data = {"fetchedAt": time.time(), "count": count, "announcements": out}
        with CACHE_LOCK:
            CACHE[code] = data
        _save_cache(code, data)
    finally:
        with CACHE_LOCK:
            PREFETCHING.pop(code, None)


def start_prefetch(code, short_name):
    with CACHE_LOCK:
        if code in PREFETCHING:
            return
        PREFETCHING[code] = True
    _threading.Thread(target=_prefetch_worker, args=(code, short_name), daemon=True).start()


def get_municipality(code, short_name):
    """Return best available snapshot; kick off a background fetch when missing/stale."""
    with CACHE_LOCK:
        cached = CACHE.get(code)
    fresh = cached and (time.time() - cached["fetchedAt"]) < _CACHE_TTL
    if not cached:
        disk = _load_disk_cache(code)
        if disk:
            cached = disk
            fresh = (time.time() - disk["fetchedAt"]) < _CACHE_TTL
            CACHE[code] = disk
    if cached:
        if not fresh:
            start_prefetch(code, short_name)          # refresh in background, serve stale now
        return {
            "status": "done",
            "refreshing": not fresh or code in PREFETCHING,
            "fetchedAt": cached["fetchedAt"],
            "count": cached["count"],
            "announcements": _attach_parking(cached["announcements"]),
        }
    start_prefetch(code, short_name)                  # cold: fetch in background
    return {"status": "prefetching", "refreshing": True,
            "fetchedAt": None, "count": None, "announcements": []}


def _proxy(method, path, body=None, params=None):
    """Forward an authenticated request to the wonkraovi API, return decoded JSON."""
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers=HEADERS)
    if body is not None:
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:300]
        except Exception:
            detail = ""
        return e.code, {"error": "upstream", "status": e.code, "detail": detail}


def _build_body(f):
    """Translate the frontend's simple query into the API's search payload."""
    loc = f.get("location") or {}
    districts = loc.get("districts") or []
    if districts:
        terms = [{"type": "DISTRICT", "code": d["code"], "shortName": d["shortName"], "classified": True}
                 for d in districts]
    else:
        terms = [{"type": "CITY", "code": loc["code"], "shortName": loc.get("shortName", loc["code"]), "classified": True}]

    return {
        "locationSearchCriteria": {"classifiedLocationTerms": terms},
        "lessorType": f.get("lessorType", "ALL"),
        "publishingTimeSearchCriteria": "ANY_DAY",
        "officeIds": [],
        "rentMin": f.get("rentMin"),
        "rentMax": f.get("rentMax"),
        "checkIfHasImages": None, "checkIfHasPanorama": None,
        "checkIfHasVideo": None, "checkIfHasShowingWithinSevenDays": None,
        "pagination": {"firstResult": 0, "maxResults": PAGE, "page": 1,
                       "sortingOrder": {"property": "PUBLISHED_OR_UPDATED_AT", "direction": "DESC"}},
        "propertyType": "RESIDENTIAL",
        "freeTextSearch": f.get("freeText", ""),
        "residentialPropertyTypes": f.get("residentialPropertyTypes") or [
            "APARTMENT_HOUSE", "ROW_HOUSE", "SEMI_DETACHED_HOUSE", "SEPARATE_HOUSE",
            "DETACHED_HOUSE", "WOODEN_HOUSE_APARTMENT", "BALCONY_ACCESS_BLOCK"],
        "roomCounts": f.get("roomCounts") or [],
        "sizeMin": f.get("sizeMin"),
        "sizeMax": f.get("sizeMax"),
        "overallConditions": [], "yearMin": None, "yearMax": None,
        "rentalAgreements": [], "rentalAvailabilities": [], "kitchenTypes": [],
        "livingFormTypes": [], "searchId": int(time.time()) % 10000000000,
        "rightOfOccupancy": f.get("rightOfOccupancy", "NO_LIVING_RIGHT"),
        "housingCompanyHasSauna": False, "housingCompanyHasElevator": False,
        "newBuildingSearchCriteria": "ALL_PROPERTIES",
    }


def _search(f):
    body = _build_body(f)
    max_results = min(int(f.get("maxResults", DEFAULT_MAX)), 300)
    announcements, count, first = [], None, 0
    while len(announcements) < max_results:
        body["pagination"]["firstResult"] = first
        body["pagination"]["page"] = (first // PAGE) + 1
        st, data = _proxy("POST", "/v3/announcements/rental/search/listpage", body=body)
        if st != 200:
            return st, data
        count = data.get("countOfAllResults", count)
        batch = data.get("announcements", [])
        announcements.extend(batch)
        if not batch or len(batch) < PAGE or len(announcements) >= (count or len(announcements)):
            break
        first += PAGE
        time.sleep(0.25)
    announcements = announcements[:max_results]   # page overshoot by one page -> trim to exact cap
    return 200, {"countOfAllResults": count, "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "announcements": _attach_parking(announcements)}


@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "explorer.html")


@app.route("/api/municipalities")
def municipalities():
    return jsonify([{"name": m["name"], "code": m["code"]} for m in MUNICIPALITIES])


@app.route("/api/districts")
def districts():
    code = request.args.get("code", "")
    st, data = _proxy("GET", "/v2/location/resolve-districts-and-subdistricts-for-municipality-code",
                      params={"municipalityCode": code})
    if st != 200:
        return jsonify({"error": "upstream", "status": st, "detail": data}), 502
    return jsonify(data)   # [{shortName, code}, ...]


@app.route("/api/search", methods=["POST"])
def search():
    f = request.get_json(force=True) or {}
    if not (f.get("location") or {}).get("code"):
        return jsonify({"error": "location.code required"}), 400
    loc = f["location"]
    # a municipality-only query is a browse "all" request -> warm the full cache
    if not (loc.get("districts") or []) and not (f.get("freeText") or "").strip():
        start_prefetch(loc["code"], loc.get("shortName", loc["code"]))
    st, data = _search(f)
    return jsonify(data), st


@app.route("/api/municipality/<code>", methods=["GET"])
def municipality(code):
    short = _MUNI_BY_CODE.get(code)
    if not short:
        return jsonify({"error": "unknown municipality code"}), 404
    return jsonify(get_municipality(code, short))


@app.route("/api/municipality/<code>/status", methods=["GET"])
def municipality_status(code):
    with CACHE_LOCK:
        cached = CACHE.get(code)
        prefetching = code in PREFETCHING
    if cached:
        return jsonify({"prefetching": prefetching, "cached": True,
                        "fetched": len(cached["announcements"]), "count": cached["count"],
                        "fetchedAt": cached["fetchedAt"]})
    return jsonify({"prefetching": prefetching, "cached": False, "fetched": 0, "count": None})


@app.route("/api/parking")
def parking():
    pk = _load_parking()
    rows = [dict(rec, latitude=k[0], longitude=k[1]) for k, rec in pk.items()]
    return jsonify({
        "count": len(rows),
        "source": "user-owned data (verified locally, NOT from vuokraovi API)",
        "csv": PARKING_CSV,
        "buildings": rows,
    })


@app.route("/api/details/<friendly_id>")
def details(friendly_id):
    st, data = _proxy("GET", "/v3/announcement/rental/details",
                      params={"friendlyId": friendly_id})
    if st != 200:
        return jsonify({"error": "upstream", "status": st, "detail": data}), 502
    return jsonify(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8933))
    app.run(host="0.0.0.0", port=port, debug=False)
