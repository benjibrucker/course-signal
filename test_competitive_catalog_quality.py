import io
import json
import threading
import unittest
from http.client import HTTPMessage
from unittest.mock import patch

import competitive_catalog as catalog


def raw_event(event_id, race_slug, **changes):
    event = {
        "id": event_id,
        "race_id": race_slug,
        "name": "Alpha 5K",
        "display_name": "5K",
        "event_date": "2026-09-14T12:00:00Z",
        "course_status": "closed",
        "live_tracking_enabled": True,
    }
    event.update(changes)
    return event


def raw_race(slug="alpha-run", name="Alpha Run", **changes):
    race = {
        "id": slug,
        "slug": slug,
        "name": name,
        "location": "Bozeman, MT",
        "race_type": "road",
        "events": [raw_event(f"{slug}-5k-2026", slug)],
    }
    race.update(changes)
    return race


def payload(*races):
    return {"status": "ok", "races": list(races)}


class FakeResponse:
    def __init__(self, body, *, final_url, status=200):
        self.body = body
        self.final_url = final_url
        self.status = status
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.final_url

    def read(self, size):
        self.read_sizes.append(size)
        return self.body


class CatalogRedirectQualityTests(unittest.TestCase):
    def test_fixed_origin_accepts_only_exact_https_api_authority(self):
        self.assertTrue(catalog._is_fixed_api_origin(catalog.CATALOG_URL))
        self.assertTrue(catalog._is_fixed_api_origin(
            "https://api.competitivetiming.com:443/races?canonical=1"
        ))
        for url in (
            "https://evil.test/races",
            "http://api.competitivetiming.com/races",
            "https://user@api.competitivetiming.com/races",
            "https://user:password@api.competitivetiming.com/races",
            "https://api.competitivetiming.com:444/races",
            "https://api.competitivetiming.com.evil.test/races",
            "https://api.competitivetiming.com./races",
            "//api.competitivetiming.com/races",
            "https://api%2ecompetitivetiming.com/races",
            "https://%61pi.competitivetiming.com/races",
        ):
            with self.subTest(url=url):
                self.assertFalse(catalog._is_fixed_api_origin(url))

    def test_redirect_handler_rejects_unsafe_target_before_following(self):
        request = catalog.urllib.request.Request(catalog.CATALOG_URL)
        handler = catalog._FixedHostRedirectHandler()
        response = io.BytesIO()
        headers = HTTPMessage()

        for allowed in (
            catalog.CATALOG_URL + "?canonical=1",
            "https://api.competitivetiming.com:443/races",
        ):
            redirected = handler.redirect_request(
                request, response, 302, "Found", headers, allowed,
            )
            self.assertIsNotNone(redirected)
            self.assertEqual(redirected.full_url, allowed)

        for rejected in (
            "https://evil.test/races",
            "http://api.competitivetiming.com/races",
            "https://user@api.competitivetiming.com/races",
            "https://api.competitivetiming.com:444/races",
            "https://api.competitivetiming.com.evil.test/races",
        ):
            with self.subTest(rejected=rejected), self.assertRaises(
                    catalog.CatalogUnavailable) as caught:
                handler.redirect_request(
                    request, response, 302, "PRIVATE_REDIRECT_ERROR", headers, rejected,
                )
            self.assertEqual(str(caught.exception), "catalog unavailable")
            self.assertNotIn(rejected, str(caught.exception))

    def test_fetch_uses_fixed_opener_and_validates_final_url_before_read(self):
        body = json.dumps({"status": "ok", "races": []}).encode()
        valid = FakeResponse(body, final_url=catalog.CATALOG_URL)
        with patch.object(catalog, "_open_fixed_host", return_value=valid, create=True) as opener, \
                patch.object(catalog.urllib.request, "urlopen",
                             side_effect=AssertionError("default urlopen must not be used")):
            self.assertEqual(catalog.fetch_catalog_payload(), {"status": "ok", "races": []})
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, catalog.CATALOG_URL)
        self.assertEqual(valid.read_sizes, [catalog.MAX_CATALOG_BYTES + 1])

        for final_url in (
            "https://evil.test/private",
            "http://api.competitivetiming.com/races",
            "https://user@api.competitivetiming.com/races",
            "https://api.competitivetiming.com:444/races",
            "https://api.competitivetiming.com.evil.test/races",
        ):
            response = FakeResponse(b"PRIVATE_RESPONSE_BODY", final_url=final_url)
            with self.subTest(final_url=final_url), \
                    patch.object(catalog, "_open_fixed_host", return_value=response,
                                 create=True) as unsafe_opener, \
                    patch.object(catalog.urllib.request, "urlopen",
                                 side_effect=AssertionError("default urlopen must not be used")), \
                    self.assertRaises(catalog.CatalogUnavailable) as caught:
                catalog.fetch_catalog_payload()
            unsafe_opener.assert_called_once()
            self.assertEqual(response.read_sizes, [])
            self.assertEqual(str(caught.exception), "catalog unavailable")
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn(final_url, str(caught.exception))
            self.assertNotIn("PRIVATE_RESPONSE_BODY", str(caught.exception))


class CatalogCacheQualityTests(unittest.TestCase):
    def test_stale_status_is_caller_local_across_interleaved_gets(self):
        now = [0.0]
        calls = [0]
        stale_returned = threading.Event()
        fresh_returned = threading.Event()
        statuses = {}
        failures = []

        def loader():
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("PRIVATE_STALE_FAILURE")
            return payload(raw_race())

        cache = catalog.CatalogCache(loader=loader, ttl_seconds=1, clock=lambda: now[0])
        cache.get()
        now[0] = 1.0

        def stale_reader():
            try:
                cache.get()
                stale_returned.set()
                if not fresh_returned.wait(1):
                    raise AssertionError("fresh reader timed out")
                statuses["stale-caller"] = cache.stale
            except BaseException as exc:
                failures.append(exc)
                stale_returned.set()
                fresh_returned.set()

        worker = threading.Thread(target=stale_reader)
        worker.start()
        self.assertTrue(stale_returned.wait(1))
        cache.get()
        statuses["fresh-caller"] = cache.stale
        fresh_returned.set()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(calls, [3])
        self.assertEqual(statuses, {"stale-caller": True, "fresh-caller": False})

    def test_backward_clock_forces_reload_instead_of_fabricating_freshness(self):
        now = [100.0]
        calls = [0]

        def loader():
            calls[0] += 1
            slug = f"race-{calls[0]}"
            return payload(raw_race(slug, f"Race {calls[0]}"))

        cache = catalog.CatalogCache(loader=loader, ttl_seconds=10, clock=lambda: now[0])
        self.assertEqual(cache.get()[0]["slug"], "race-1")
        now[0] = 90.0
        self.assertEqual(cache.get()[0]["slug"], "race-2")
        self.assertEqual(calls, [2])
        self.assertFalse(cache.stale)

    def test_invalid_clock_values_fail_generically_before_loading(self):
        for bad_clock in (True, float("nan"), float("inf"), -1.0, "100"):
            calls = []
            cache = catalog.CatalogCache(
                loader=lambda: calls.append(True) or payload(raw_race()),
                clock=lambda value=bad_clock: value,
            )
            with self.subTest(clock=bad_clock), self.assertRaises(
                    catalog.CatalogUnavailable) as caught:
                cache.get()
            self.assertEqual(str(caught.exception), "catalog unavailable")
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(calls, [])
            self.assertFalse(cache.stale)


class CatalogNormalizationQualityTests(unittest.TestCase):
    def test_only_literal_true_enables_live_tracking(self):
        absent = object()
        values = (True, False, None, absent, "false", "true", 0, 1, [], {})
        races = []
        for index, value in enumerate(values):
            slug = f"race-{index}"
            event = raw_event(f"event-{index}", slug, live_tracking_enabled=value)
            if value is absent:
                event.pop("live_tracking_enabled")
            races.append(raw_race(slug, f"Race {index}", events=[event]))

        rows = catalog.sanitize_catalog(payload(*races))
        by_slug = {row["slug"]: row["events"][0]["live_tracking_enabled"] for row in rows}
        self.assertTrue(by_slug["race-0"])
        for index in range(1, len(values)):
            self.assertFalse(by_slug[f"race-{index}"])

    def test_catalog_structural_caps_fail_closed_for_hostile_arrays(self):
        with self.assertRaises(catalog.CatalogUnavailable):
            catalog.sanitize_catalog({
                "status": "ok",
                "races": [None] * (catalog.MAX_CATALOG_RACES + 1),
            })

        oversized_events = raw_race(events=[None] * (catalog.MAX_EVENTS_PER_RACE + 1))
        with self.assertRaises(catalog.CatalogUnavailable):
            catalog.sanitize_catalog(payload(oversized_events))

        per_race = min(catalog.MAX_EVENTS_PER_RACE, catalog.MAX_TOTAL_CATALOG_EVENTS)
        race_count = catalog.MAX_TOTAL_CATALOG_EVENTS // per_race + 1
        aggregate = [
            {"events": [None] * per_race}
            for _ in range(race_count)
        ]
        with self.assertRaises(catalog.CatalogUnavailable):
            catalog.sanitize_catalog(payload(*aggregate))

    def test_oversized_searchable_scalar_fails_closed_without_normalization(self):
        hostile = raw_race(name="x" * (catalog.MAX_SOURCE_TEXT_CHARS + 1))
        self.assertEqual(catalog.sanitize_catalog(payload(hostile)), [])


class CatalogSearchQualityTests(unittest.TestCase):
    class DiscardedValue:
        def __str__(self):
            return "nowhere"

        def __deepcopy__(self, memo):
            raise AssertionError("discarded row was deep-copied")

    def test_search_parameters_are_bounded_before_normalization(self):
        rows = [raw_race()]
        with self.assertRaises(catalog.CatalogQueryError):
            catalog.search_catalog(rows, "alpha" + " " * 10_000)
        with self.assertRaises(catalog.CatalogQueryError):
            catalog.search_catalog(rows, "alpha", year="2" * 10_000)
        with self.assertRaises(catalog.CatalogQueryError):
            catalog.search_catalog(rows, "alpha", limit="9" * 10_000)

    def test_search_deepcopies_only_capped_winners(self):
        races = [
            {
                "slug": f"alpha-{index:02d}",
                "name": f"Alpha {index:02d}",
                "location": "Bozeman, MT",
                "race_type": "road",
                "events": [],
            }
            for index in range(catalog.MAX_SEARCH_RESULTS)
        ]
        races.append({
            "slug": "zeta-alpha",
            "name": "Zeta alpha",
            "location": self.DiscardedValue(),
            "race_type": "road",
            "events": [],
        })

        result = catalog.search_catalog(races, "alpha", limit=10_000)

        self.assertEqual(len(result), catalog.MAX_SEARCH_RESULTS)
        self.assertNotIn("zeta-alpha", [row["slug"] for row in result])

    def test_search_preserves_source_order_for_equal_rank_keys(self):
        tied = [
            {"slug": "same", "name": "Alpha", "location": location,
             "race_type": "road", "events": []}
            for location in ("first", "second", "third")
        ]
        result = catalog.search_catalog(tied, "alpha", limit=2)
        self.assertEqual([row["location"] for row in result], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
