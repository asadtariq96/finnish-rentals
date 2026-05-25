"""Vuokraovi (vuokraovi.com) scraper — no auth, no browser.

Reverse-engineered 2026-05-25 via browser-harness.

Two endpoints, both open (only require x-portal-identifier + x-uid headers):

  POST /distant/swordsman/v3/announcements/rental/search/listpage  -> search results
  GET  /distant/swordsman/v3/announcement/rental/details?friendlyId=<id>  -> full listing

Usage:
    python3 vuokraovi.py search                 # 1st page of the Espoo search
    python3 vuokraovi.py detail dz6727          # full details for one listing
    python3 vuokraovi.py all                    # walk all pages, fetch every detail
"""
import json
import sys
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API = "https://api.vuokraovi.com/distant/swordsman/v3"
UID = str(uuid.uuid4())  # any UUID works; stable per process
HEADERS = {
    "x-portal-identifier": "VUOKRAOVI",
    "x-uid": UID,
    "Referer": "https://www.vuokraovi.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _request(url, *, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(HEADERS)
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# ---------- search ----------

def search_payload(*, city="Espoo", city_code="FI_UUSIMAA_ESPOO",
                   rent_max=None, rent_min=None, size_min=None, size_max=None,
                   year_min=None, room_counts=None, page=1, per_page=30):
    """Minimal payload for the listpage POST. Extend filters as needed."""
    return {
        "locationSearchCriteria": {
            "classifiedLocationTerms": [{
                "type": "CITY", "code": city_code, "shortName": city,
                "parentCountryCode": "FI", "parentRegionCode": "FI_UUSIMAA",
                "parentRegionName": "Uusimaa", "fullName": city,
                "latitude": 60.205919, "longitude": 24.65805, "classified": True,
            }],
        },
        "lessorType": "ALL",
        "publishingTimeSearchCriteria": "ANY_DAY",
        "officeIds": [],
        "rentMin": rent_min, "rentMax": rent_max,
        "sizeMin": size_min, "sizeMax": size_max,
        "yearMin": year_min, "yearMax": None,
        "checkIfHasImages": None, "checkIfHasPanorama": None,
        "checkIfHasVideo": None, "checkIfHasShowingWithinSevenDays": None,
        "pagination": {
            "firstResult": (page - 1) * per_page,
            "maxResults": per_page,
            "page": page,
            "sortingOrder": {"property": "PUBLISHED_OR_UPDATED_AT", "direction": "DESC"},
        },
        "propertyType": "RESIDENTIAL",
        "freeTextSearch": "",
        "residentialPropertyTypes": [
            "APARTMENT_HOUSE", "ROW_HOUSE", "SEMI_DETACHED_HOUSE",
            "SEPARATE_HOUSE", "DETACHED_HOUSE", "WOODEN_HOUSE_APARTMENT",
            "BALCONY_ACCESS_BLOCK",
        ],
        "roomCounts": room_counts or [],
        "overallConditions": [], "rentalAgreements": [],
        "rentalAvailabilities": [], "kitchenTypes": [], "livingFormTypes": [],
        "rightOfOccupancy": "NO_LIVING_RIGHT",
        "newBuildingSearchCriteria": "ALL_PROPERTIES",
    }


def search_page(**filters):
    """Return {'countOfAllResults': int, 'announcements': [...]}"""
    return _request(f"{API}/announcements/rental/search/listpage",
                    method="POST", body=search_payload(**filters))


def search_all(*, delay=0.4, **filters):
    """Iterator over every announcement across all pages."""
    page = 1
    while True:
        result = search_page(page=page, **filters)
        annos = result["announcements"]
        for a in annos:
            yield a
        total = result["countOfAllResults"]
        if page * filters.get("per_page", 30) >= total or not annos:
            return
        page += 1
        time.sleep(delay)  # be polite


# ---------- detail ----------

def detail(friendly_id):
    return _request(f"{API}/announcement/rental/details?friendlyId={friendly_id}")


# ---------- feature filters (shared with oikotie.py via finnish_rental.py) ----------

from finnish_rental import PARK_GARAGE_RE as _PARK_GARAGE_RE, EV_CHARGE_RE as _EV_CHARGE_RE
from finnish_rental import has_parking_garage as _has_park, has_ev_charging as _has_ev


def has_parking_garage(d):
    """True if the listing's free text indicates a parking hall/garage."""
    return _has_park(d.get("text") or "")


def has_ev_charging(d):
    """True if the listing's free text indicates EV charging is available."""
    return _has_ev(d.get("text") or "")


def matches_parking_and_ev(d):
    return has_parking_garage(d) and has_ev_charging(d)


# ---------- CLI ----------

def _print_listing_summary(a):
    print(f"  {a['friendlyId']:>8}  €{a.get('searchRent') or '?':>5}  "
          f"{a.get('area') or '?':>5}m²  {a.get('roomCount','?'):>3}r  "
          f"{a.get('addressLine1','?')}, {a.get('addressLine2','?')}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "search"

    if cmd == "search":
        # Reproduces the search params from m2481096690 (Espoo, rent<=1350, size>=59, 3+ rooms, 2020+)
        result = search_page(
            rent_max=1350, size_min=59, year_min=2020,
            room_counts=["THREE_ROOMS", "FOUR_ROOMS", "FIVE_ROOMS", "MORE_THAN_FIVE_ROOMS"],
        )
        print(f"total matching: {result['countOfAllResults']}")
        for a in result["announcements"]:
            _print_listing_summary(a)

    elif cmd == "detail":
        d = detail(sys.argv[2])
        # Project to a flat summary; full JSON still available via -- raw
        prop = d.get("property", {})
        res = d.get("residenceDetailsDTO", {})
        c = d.get("announcementContactInfo", {})
        print(json.dumps({
            "friendlyId": d["friendlyId"],
            "url": f"https://www.vuokraovi.com/en/item/{d['friendlyId']}",
            "rent": d.get("rent"),
            "deposit": d.get("rentalDeposit"),
            "available": d.get("rentalAvailability"),
            "size_m2": res.get("livingArea"),
            "rooms": res.get("roomCount"),
            "address": {
                "street": prop.get("street"), "houseNumber": prop.get("houseNumber"),
                "district": prop.get("district"), "municipality": prop.get("municipality"),
                "postCode": prop.get("postCode"),
            },
            "geo": prop.get("geoCode"),
            "pets_allowed": d.get("petsAllowed"),
            "smoking_allowed": d.get("smokingAllowed"),
            "office": c.get("officeName"),
            "office_phone": c.get("officePhoneNumber"),
            "image_count": len(d.get("imageIds", [])),
            "text": (d.get("text") or "")[:200] + "...",
        }, indent=2, ensure_ascii=False))

    elif cmd == "all":
        for a in search_all(rent_max=1350, size_min=59, year_min=2020,
                            room_counts=["THREE_ROOMS","FOUR_ROOMS","FIVE_ROOMS","MORE_THAN_FIVE_ROOMS"]):
            d = detail(a["friendlyId"])
            print(json.dumps({"friendlyId": a["friendlyId"],
                              "rent": d.get("rent"),
                              "size": d.get("residenceDetailsDTO",{}).get("livingArea"),
                              "street": d.get("property",{}).get("street"),
                              "office": d.get("announcementContactInfo",{}).get("officeName")},
                             ensure_ascii=False))
            time.sleep(0.4)

    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except HTTPError as e:
        print(f"HTTP {e.code}: {e.read()[:300].decode('utf-8','replace')}", file=sys.stderr)
        sys.exit(1)
