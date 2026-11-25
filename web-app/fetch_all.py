#!/usr/bin/env python3
"""Fetch all rental listings from vuokraovi.com search API, paginated."""
import json, time, urllib.request

BASE = "https://api.vuokraovi.com/distant/swordsman/v3/announcements/rental/search/listpage"
HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://www.vuokraovi.com",
    "referer": "https://www.vuokraovi.com/",
    "x-portal-identifier": "VUOKRAOVI",
    "x-uid": "dea18320-28fa-42c0-b6b8-1129ec34552c",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
}

with open("body.json") as f:
    base_body = json.load(f)

announcements = []
count_of_all = None
page = 1
while True:
    body = dict(base_body)
    body["pagination"] = {
        "firstResult": (page - 1) * 30,
        "maxResults": 30,
        "page": page,
        "sortingOrder": {"property": "PUBLISHED_OR_UPDATED_AT", "direction": "DESC"},
    }
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    count_of_all = data.get("countOfAllResults", count_of_all)
    batch = data.get("announcements", [])
    announcements.extend(batch)
    print(f"page {page}: got {len(batch)} (total so far {len(announcements)} / {count_of_all})")
    if len(announcements) >= count_of_all or not batch:
        break
    page += 1
    time.sleep(0.3)

out = {"countOfAllResults": count_of_all, "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "announcements": announcements}
with open("listings.json", "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print(f"\nSaved {len(announcements)} listings to listings.json")
