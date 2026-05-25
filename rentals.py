"""Unified Espoo rental search across Vuokraovi + Oikotie.

Usage:
    python3 rentals.py matches                          # both sites, parking + EV, default filters
    python3 rentals.py matches --source oikotie         # one site only
    python3 rentals.py matches --rent-max 1500 --size-min 50 --rooms 2,3
    python3 rentals.py search                           # just list first page from both
    python3 rentals.py detail oikotie:24502080          # full JSON for one listing
    python3 rentals.py detail vuokraovi:dz6727

Filters (all optional, applied to both sites):
    --rent-max  MAX     max €/month                                 (default 1350)
    --size-min  MIN     min m²                                       (default 60)
    --rooms     CSV     room counts to include, e.g. 3 or 3,4,5+    (default 3)
    --source    S       vuokraovi | oikotie | both                  (default both)
    --no-parking        skip the parking-garage filter
    --no-ev             skip the EV-charging filter
    --json              emit JSON instead of a table
"""
import argparse
import json
import re
import sys

import vuokraovi
import oikotie
from finnish_rental import has_parking_garage, has_ev_charging


ROOM_MAP = {  # CLI room count -> Vuokraovi enum
    "3": "THREE_ROOMS", "4": "FOUR_ROOMS", "5": "FIVE_ROOMS",
    "5+": "MORE_THAN_FIVE_ROOMS",
}


# ---------- normalize listings to a shared shape ----------

def _vuokraovi_normalize(card, det):
    """Vuokraovi: announcement card from listpage + full detail."""
    prop = det.get("property", {}) or {}
    res = det.get("residenceDetailsDTO", {}) or {}
    street = (prop.get("street") or {}).get("defaultName") or ""
    house = prop.get("houseNumber") or ""
    stair = prop.get("stairwayAndApartment") or ""
    district = (prop.get("district") or {}).get("defaultName") or ""
    text = det.get("text") or ""
    return {
        "source": "vuokraovi",
        "id": det.get("friendlyId"),
        "url": f"https://www.vuokraovi.com/en/item/{det.get('friendlyId')}",
        "rent": det.get("rent") or 0,
        "size": res.get("livingArea") or 0,
        "rooms": (res.get("roomCount") or "").replace("_ROOMS", ""),
        "district": district,
        "street": street,
        "house": house,
        "stair": stair,
        "address_full": f"{street} {house}".strip(),
        "text": text,
        "has_parking": has_parking_garage(text),
        "has_ev": has_ev_charging(text),
    }


def _oikotie_normalize(card, det):
    """Oikotie: card from search + full /api/card/X detail."""
    loc = card.get("location", {}) or {}
    data = card.get("data", {}) or {}
    text = (det.get("adData", {}) or {}).get("description") or ""
    price = _parse_price(data.get("price"))
    size = _parse_size(data.get("size"))
    addr = loc.get("address") or ""
    street, house, stair = _split_address(addr)
    return {
        "source": "oikotie",
        "id": str(card.get("cardId")),
        "url": card.get("url") or f"https://asunnot.oikotie.fi/vuokra-asunnot/espoo/{card.get('cardId')}",
        "rent": price,
        "size": size,
        "rooms": str(data.get("rooms") or ""),
        "district": loc.get("district") or "",
        "street": street,
        "house": house,
        "stair": stair,
        "address_full": addr,
        "text": text,
        "has_parking": has_parking_garage(text),
        "has_ev": has_ev_charging(text),
    }


def _parse_price(p):
    if not p: return 0
    digits = re.sub(r"\D", "", p)
    return int(digits) if digits else 0


def _parse_size(s):
    if not s: return 0
    m = re.search(r"(\d+(?:[.,]\d+)?)", s.replace(",", "."))
    return float(m.group(1)) if m else 0


def _split_address(full):
    """Parse 'Karakalliontie 8 C' -> ('Karakalliontie', '8', 'C')."""
    if not full: return "", "", ""
    m = re.match(r"^(.+?)\s+(\d+\w*)\s*([A-Za-z])?$", full.strip())
    if m:
        return m.group(1).strip(), m.group(2), (m.group(3) or "").upper()
    return full.strip(), "", ""


# ---------- dedup ----------

def _dedup_key(item):
    """Same building+stairwell -> same key, regardless of site or formatting."""
    street = re.sub(r"\s+", " ", item["street"].lower().strip())
    house = item["house"].lower()
    # Stairwell is *unit*-level; keep it in the key (different stairwells = different sub-buildings)
    stair = item["stair"]
    return (street, house, stair)


# ---------- pull + filter ----------

def fetch_vuokraovi(rent_max, size_min, rooms):
    filters = dict(
        rent_max=rent_max, size_min=size_min,
        room_counts=[ROOM_MAP[r] for r in rooms if r in ROOM_MAP],
    )
    out = []
    for a in vuokraovi.search_all(**filters):
        d = vuokraovi.detail(a["friendlyId"])
        out.append(_vuokraovi_normalize(a, d))
    return out


def fetch_oikotie(rent_max, size_min, rooms):
    room_codes = [int(r) for r in rooms if r.isdigit()]
    cards = list(oikotie.search_all(
        price_max=rent_max, size_min=size_min, roomCount=room_codes,
        buildingType=[1, 2, 4, 8, 32, 64, 128, 256, 512], secondarySearchType=1,
    ))
    out = []
    for c in cards:
        d = oikotie.detail(c["cardId"])
        out.append(_oikotie_normalize(c, d))
    return out


# ---------- CLI ----------

def cmd_matches(args):
    rooms = args.rooms.split(",")
    items = []
    if args.source in ("both", "vuokraovi"):
        print(f"fetching vuokraovi...", file=sys.stderr)
        items.extend(fetch_vuokraovi(args.rent_max, args.size_min, rooms))
    if args.source in ("both", "oikotie"):
        print(f"fetching oikotie...", file=sys.stderr)
        items.extend(fetch_oikotie(args.rent_max, args.size_min, rooms))

    # Apply parking/EV filters
    if not args.no_parking:
        items = [i for i in items if i["has_parking"]]
    if not args.no_ev:
        items = [i for i in items if i["has_ev"]]

    # Dedup: group by (street, house, stair), pick cheapest, note other source(s)
    groups = {}
    for it in items:
        groups.setdefault(_dedup_key(it), []).append(it)
    deduped = []
    for variants in groups.values():
        variants.sort(key=lambda x: x["rent"])
        winner = variants[0]
        other_sources = sorted({v["source"] for v in variants[1:]} - {winner["source"]})
        winner["also_on"] = ",".join(other_sources) if other_sources else ""
        deduped.append(winner)
    deduped.sort(key=lambda x: x["rent"])

    if args.json:
        # strip raw text to keep output sane
        for it in deduped: it.pop("text", None)
        print(json.dumps(deduped, indent=2, ensure_ascii=False))
        return

    print(f"\n{len(items)} matches before dedup, {len(deduped)} unique buildings\n")
    print(f"{'src':<10} {'€/mo':>5} {'m²':>5} {'r':>2}  {'district':<14} {'address':<32} {'also':<10} url")
    print("-" * 130)
    for it in deduped:
        also = it.get("also_on") or ""
        print(f"{it['source']:<10} €{it['rent']:>4.0f} {it['size']:>5.0f} {it['rooms']:>2}  "
              f"{it['district'][:14]:<14} {it['address_full'][:32]:<32} "
              f"{also:<10} {it['url']}")


def cmd_search(args):
    if args.source in ("both", "vuokraovi"):
        r = vuokraovi.search_page(rent_max=args.rent_max, size_min=args.size_min,
                                   room_counts=[ROOM_MAP[r] for r in args.rooms.split(",") if r in ROOM_MAP])
        print(f"\nVUOKRAOVI: {r['countOfAllResults']} matching")
        for a in r["announcements"][:10]:
            print(f"  {a['friendlyId']:<8} €{a.get('searchRent','?'):>5} "
                  f"{a.get('area','?'):>5}m²  {a.get('addressLine1','?')}")
    if args.source in ("both", "oikotie"):
        rooms = [int(r) for r in args.rooms.split(",") if r.isdigit()]
        r = oikotie.search_page(price_max=args.rent_max, size_min=args.size_min,
                                roomCount=rooms,
                                buildingType=[1, 2, 4, 8, 32, 64, 128, 256, 512],
                                secondarySearchType=1, limit=10)
        print(f"\nOIKOTIE: {r['found']} matching")
        for c in r["cards"][:10]:
            data = c.get("data", {})
            print(f"  {c['cardId']:>9} {data.get('price','?'):>14} "
                  f"{data.get('size','?'):>7}  {c.get('location',{}).get('address','?')}")


def cmd_detail(args):
    source, _, lid = args.id.partition(":")
    if source == "vuokraovi":
        d = vuokraovi.detail(lid)
        text = d.get("text", "")
    elif source == "oikotie":
        d = oikotie.detail(int(lid))
        text = (d.get("adData", {}) or {}).get("description", "")
    else:
        sys.exit(f"unknown source: {source!r} (use 'vuokraovi:ID' or 'oikotie:ID')")
    print(f"parking_garage: {has_parking_garage(text)}")
    print(f"ev_charging:    {has_ev_charging(text)}")
    print(f"\n--- description ({len(text)} chars) ---")
    print(text)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", choices=["both", "vuokraovi", "oikotie"], default="both")
    common.add_argument("--rent-max", type=int, default=1350)
    common.add_argument("--size-min", type=int, default=60)
    common.add_argument("--rooms", default="3", help="csv of room counts (e.g. 3,4,5+)")

    m = sub.add_parser("matches", parents=[common], help="filtered + parking/EV")
    m.add_argument("--no-parking", action="store_true")
    m.add_argument("--no-ev", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_matches)

    s = sub.add_parser("search", parents=[common], help="first page of raw results")
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("detail", help="full description for a single listing")
    d.add_argument("id", help="vuokraovi:dz6727 or oikotie:24502080")
    d.set_defaults(func=cmd_detail)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
