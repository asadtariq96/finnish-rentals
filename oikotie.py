"""Oikotie (asunnot.oikotie.fi) rental scraper.

Reverse-engineered 2026-05-25 via browser-harness.

Endpoints (all on https://asunnot.oikotie.fi):
  GET /api/search?<URL search params>           -> {found, start, cards[]}
  GET /api/card/<cardId>                        -> full listing detail

Auth: three custom headers read from <meta> tags on any oikotie.fi page:
  OTA-token  (meta name="api-token")
  OTA-loaded (meta name="loaded")
  OTA-cuid   (meta name="cuid")

Tokens are per-session and likely rotate. Refresh via refresh_tokens() if you get 401.
"""
import json
import re
import sys
import time
import urllib.request
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "https://asunnot.oikotie.fi"

# Default tokens captured 2026-05-25 — may expire; override via refresh_tokens().
TOKENS = {
    "OTA-token":  "8fa42328e98d3326fd44e83762ab856a58e95292daade2b7436a28bf5bbdf89a",
    "OTA-loaded": "1779719947",
    "OTA-cuid":   "96bbd05bf39efa553b062f0efbc0cd1579c2eaed",
}


def _headers():
    return {
        **TOKENS,
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Referer": "https://asunnot.oikotie.fi/vuokra-asunnot",
    }


def refresh_tokens():
    """Fetch the public landing page and re-read OTA-* tokens from <meta>. Updates TOKENS in place."""
    req = Request(f"{BASE}/vuokra-asunnot", headers={"User-Agent": _headers()["User-Agent"]})
    with urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", "replace")
    for meta_name, header_name in [("api-token", "OTA-token"), ("loaded", "OTA-loaded"), ("cuid", "OTA-cuid")]:
        m = re.search(rf'<meta\s+name=["\']{meta_name}["\']\s+content=["\']([^"\']+)["\']', html)
        if m:
            TOKENS[header_name] = m.group(1)
    return dict(TOKENS)


def _get(url):
    req = Request(url, headers=_headers())
    try:
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except HTTPError as e:
        if e.code == 401:
            refresh_tokens()
            req = Request(url, headers=_headers())
            with urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        raise


# ---------- search ----------

def search_page(*, offset=0, limit=100, **filters):
    """One page of search results.
       Pagination is `offset=N` (not `pagination=N` — that param is ignored by the API).
       `limit` controls page size; up to ~500 in one request.
    """
    params = []
    for k, v in filters.items():
        key = k.replace("_max", "[max]").replace("_min", "[min]")
        if isinstance(v, list):
            for item in v:
                params.append((f"{key}[]", item))
        else:
            params.append((key, v))
    params.append(("locations", '[[39,6,"Espoo"]]'))
    params.append(("cardType", 101))
    params.append(("offset", offset))
    params.append(("limit", limit))
    qs = urlencode(params)
    return _get(f"{BASE}/api/search?{qs}")


def search_all(*, page_size=500, **filters):
    """Iterator over every card. Default page_size pulls everything in one call."""
    offset = 0
    while True:
        result = search_page(offset=offset, limit=page_size, **filters)
        cards = result.get("cards", [])
        if not cards:
            return
        for c in cards:
            yield c
        if len(cards) < page_size:
            return  # last page
        offset += page_size
        time.sleep(0.3)


# ---------- detail ----------

def detail(card_id):
    return _get(f"{BASE}/api/card/{card_id}")


# ---------- feature filters (shared with vuokraovi.py via finnish_rental.py) ----------

from finnish_rental import has_parking_garage as _has_park, has_ev_charging as _has_ev


def description_text(d):
    """Return the free-text description from an oikotie detail."""
    return (d.get("adData", {}) or {}).get("description") or ""


def has_parking_garage(d):
    return _has_park(description_text(d))


def has_ev_charging(d):
    return _has_ev(description_text(d))


# ---------- CLI ----------

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "search"

    user_filters = dict(
        price_max=1350, size_min=60, roomCount=[3],
        buildingType=[1, 2, 4, 8, 32, 64, 128, 256, 512],
        secondarySearchType=1,
    )

    if cmd == "search":
        result = search_page(page=1, **user_filters)
        print(f"found total: {result['found']}, page 1: {len(result['cards'])}")
        for c in result["cards"][:20]:
            loc = c.get("location", {})
            data = c.get("data", {})
            print(f"  {c['cardId']:>9}  {data.get('price','?'):>14}  "
                  f"{data.get('size','?'):>6}  {data.get('rooms','?')}r  "
                  f"{loc.get('district','?'):<20} {loc.get('address','?')}")

    elif cmd == "detail":
        d = detail(int(sys.argv[2]))
        print(json.dumps({
            "cardId": d.get("cardId"),
            "url": d.get("links", {}).get("url"),
            "description": description_text(d)[:400],
            "has_parking": has_parking_garage(d),
            "has_ev": has_ev_charging(d),
        }, indent=2, ensure_ascii=False))

    elif cmd == "all":
        for c in search_all(**user_filters):
            print(c["cardId"], c.get("location", {}).get("address"))

    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
