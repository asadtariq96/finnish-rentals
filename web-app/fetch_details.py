#!/usr/bin/env python3
"""Fetch full detail (description etc.) for every listing in listings.json."""
import json, time, urllib.request, urllib.error

HEADERS = {
    "accept": "application/json",
    "origin": "https://www.vuokraovi.com",
    "referer": "https://www.vuokraovi.com/",
    "x-portal-identifier": "VUOKRAOVI",
    "x-uid": "dea18320-28fa-42c0-b6b8-1129ec34552c",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
}
BASE = "https://api.vuokraovi.com/distant/swordsman/v3/announcement/rental/details"

with open("listings.json") as f:
    anns = json.load(f)["announcements"]

# compile all friendlyIds (dedupe)
seen = {}
for a in anns:
    seen.setdefault(a["friendlyId"], a["id"])

results = []
errors = []
for idx, (friendly, aid) in enumerate(seen.items(), 1):
    url = f"{BASE}?friendlyId={friendly}"
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read())
        d["_id"] = aid
        results.append(d)
        print(f"[{idx}/{len(seen)}] {friendly} OK text={len(d.get('text',''))}")
    except Exception as e:
        errors.append((friendly, str(e)))
        print(f"[{idx}/{len(seen)}] {friendly} ERROR {e}")
    time.sleep(0.4)

out = {
    "count": len(results),
    "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "details": results,
    "errors": errors,
}
with open("details.json", "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print(f"\nSaved {len(results)} details to details.json (errors: {len(errors)})")
