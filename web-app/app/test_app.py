"""Unit tests for the vuokraovi live-search backend (build/search/paging logic).

The upstream API is mocked via app._proxy so tests are hermetic and fast.
Run from app/:  ../appenv/bin/python -m unittest -v test_app
"""
import unittest, time
from unittest import mock

import app as appmod


def page_of(size, start_id=1):
    return [{"id": start_id + i, "friendlyId": f"f{start_id + i}", "latitude": 0, "longitude": 0}
            for i in range(size)]


class BuildBodyTest(unittest.TestCase):
    def test_city_location_term(self):
        b = appmod._build_body({"location": {"code": "FI_UUSIMAA_ESPOO", "shortName": "Espoo", "districts": []}})
        terms = b["locationSearchCriteria"]["classifiedLocationTerms"]
        self.assertEqual(terms, [{"type": "CITY", "code": "FI_UUSIMAA_ESPOO", "shortName": "Espoo", "classified": True}])

    def test_district_location_terms(self):
        b = appmod._build_body({"location": {"code": "FI_UUSIMAA_ESPOO", "shortName": "Espoo",
                                              "districts": [{"code": "FI_UUSIMAA_ESPOO_SUVELA", "shortName": "Suvela"}]}})
        terms = b["locationSearchCriteria"]["classifiedLocationTerms"]
        self.assertEqual(terms, [{"type": "DISTRICT", "code": "FI_UUSIMAA_ESPOO_SUVELA", "shortName": "Suvela", "classified": True}])

    def test_filters_plumbed(self):
        b = appmod._build_body({"location": {"code": "FI_PIRKANMAA_TAMPERE", "shortName": "Tampere", "districts": []},
                                "rentMax": 900, "sizeMin": 50, "sizeMax": 80,
                                "roomCounts": ["THREE_ROOMS"], "freeText": "otaniemi", "rightOfOccupancy": "NO_LIVING_RIGHT"})
        self.assertEqual(b["rentMax"], 900)
        self.assertEqual(b["sizeMin"], 50)
        self.assertEqual(b["sizeMax"], 80)
        self.assertEqual(b["roomCounts"], ["THREE_ROOMS"])
        self.assertEqual(b["freeTextSearch"], "otaniemi")
        # empty roomCounts + new-building enum always ALL_PROPERTIES (only valid value)
        b2 = appmod._build_body({"location": {"code": "X", "shortName": "x", "districts": []}})
        self.assertEqual(b2["roomCounts"], [])
        self.assertEqual(b2["newBuildingSearchCriteria"], "ALL_PROPERTIES")


class SearchPagingTest(unittest.TestCase):
    def _mock_proxy(self, total, per_page=30):
        """Fake _proxy that pages a synthetic 'total'-sized result set."""
        calls = {"n": 0}

        def fake(method, path, body=None, params=None):
            first = body["pagination"]["firstResult"]
            batch = page_of(min(per_page, total - first), start_id=first + 1)
            calls["n"] += 1
            return 200, {"countOfAllResults": total, "announcements": batch}

        return fake, calls

    def test_gathers_all_pages_under_cap(self):
        fake, calls = self._mock_proxy(130)
        with mock.patch.object(appmod, "_proxy", side_effect=fake):
            st, data = appmod._search({"location": {"code": "X", "shortName": "x", "districts": []}, "maxResults": 200})
        self.assertEqual(st, 200)
        self.assertEqual(len(data["announcements"]), 130)
        self.assertEqual(data["countOfAllResults"], 130)

    def test_caps_at_requested_max(self):
        fake, calls = self._mock_proxy(5000)
        with mock.patch.object(appmod, "_proxy", side_effect=fake):
            st, data = appmod._search({"location": {"code": "X", "shortName": "x", "districts": []}, "maxResults": 100})
        self.assertEqual(len(data["announcements"]), 100)  # paginates to max, not all 5000

    def test_upstream_error_passthrough(self):
        def fail(method, path, body=None, params=None):
            return 400, {"error": "upstream"}
        with mock.patch.object(appmod, "_proxy", side_effect=fail):
            st, data = appmod._search({"location": {"code": "X", "shortName": "x", "districts": []}})
        self.assertEqual(st, 400)
        self.assertEqual(data["error"], "upstream")


class PrefetchCacheTest(unittest.TestCase):
    def setUp(self):
        appmod.CACHE.clear()
        appmod.PREFETCHING.clear()
        # never hit the real network from tests
        self.worker = mock.patch.object(appmod, "_prefetch_worker", return_value=None)
        self.worker.start()
        self.addCleanup(self.worker.stop)

    def test_cold_municipality_starts_background_fetch(self):
        with mock.patch.object(appmod, "_load_disk_cache", return_value=None):   # no disk cache
            r = appmod.get_municipality("FI_UUSIMAA_ESPOO", "Espoo")
        self.assertEqual(r["status"], "prefetching")
        self.assertEqual(r["announcements"], [])

    def test_warm_cache_served_without_refresh(self):
        appmod.CACHE["FI_UUSIMAA_ESPOO"] = {"fetchedAt": time.time(), "count": 1883, "announcements": [1, 2, 3]}
        r = appmod.get_municipality("FI_UUSIMAA_ESPOO", "Espoo")
        self.assertEqual(r["status"], "done")

    def test_fresh_cache_served_without_refresh(self):
        appmod.CACHE["X"] = {"fetchedAt": time.time(), "count": 2, "announcements": [{}, {}]}
        with mock.patch.object(appmod, "start_prefetch") as sp:
            r = appmod.get_municipality("X", "x")
        self.assertEqual(r["status"], "done")
        self.assertFalse(r["refreshing"])
        sp.assert_not_called()

    def test_stale_cache_served_but_refreshes(self):
        appmod.CACHE["X"] = {"fetchedAt": time.time() - 1_000_000, "count": 1, "announcements": [1]}
        with mock.patch.object(appmod, "start_prefetch") as sp:
            r = appmod.get_municipality("X", "x")
        self.assertEqual(r["status"], "done")
        self.assertTrue(r["refreshing"])     # stale -> refreshed in background
        sp.assert_called_once_with("X", "x")


class RoutesTest(unittest.TestCase):
    def test_routes_registered(self):
        appmod.app.config["TESTING"] = True
        client = appmod.app.test_client()
        # municipalities + districts return 200-shaped responses (districts mocks upstream)
        self.assertEqual(client.get("/api/municipalities").status_code, 200)
        with mock.patch.object(appmod, "_proxy", return_value=(200, [{"shortName": "X", "code": "X"}])):
            r = client.get("/api/districts?code=Foo")
            self.assertEqual(r.status_code, 200)
        # search requires location.code
        self.assertEqual(client.post("/api/search", json={"location": {}}).status_code, 400)
        # index serves html
        self.assertIn("Live search", client.get("/").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
