import datetime as dt
import io
import json
import threading
import unittest
import urllib.request
from email.message import Message
from unittest.mock import patch

import server


NOW = dt.datetime(2026, 9, 14, 18, 0, tzinfo=dt.timezone.utc)
EVENT_ID = "alpha-run-5k-2026"


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, body=b"{}", *, final_url=None, status=200):
        super().__init__(body)
        self.status = status
        self.final_url = final_url
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)

    def geturl(self):
        return self.final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def assert_recursive_absent(testcase, value, *sentinels):
    if isinstance(value, dict):
        for key, nested in value.items():
            assert_recursive_absent(testcase, key, *sentinels)
            assert_recursive_absent(testcase, nested, *sentinels)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            assert_recursive_absent(testcase, nested, *sentinels)
    else:
        rendered = str(value)
        for sentinel in sentinels:
            testcase.assertNotIn(sentinel, rendered)


def minimal_bundle(*, live_tracking_enabled=False, maps=None):
    return {
        "event_response": {"event": {
            "id": EVENT_ID,
            "name": "Alpha Run",
            "display_name": "5K",
            "event_date": "2026-09-14T12:00:00Z",
            "start_time": "08:00:00",
            "timezone": "America/Denver",
            "course_status": "active",
            "split_names": ["Start", "Finish"],
            "live_tracking_enabled": live_tracking_enabled,
        }},
        "course_response": {"courseMaps": maps or []},
        "gps_response": {"positions": []},
        "leaderboard": [],
        "history": [],
        "history_status": "unavailable",
        "history_error": "history: unavailable",
        "history_fetched_at": None,
        "source_freshness": {},
        "errors": [],
        "upstream_stale": False,
    }


class FixedUpstreamRedirectTests(unittest.TestCase):
    def test_redirect_policy_rejects_unsafe_origins_before_following(self):
        expected = server.UPSTREAM + f"/events/{EVENT_ID}"
        request = urllib.request.Request(expected)
        handler = server._FixedUpstreamRedirectHandler()
        response = io.BytesIO()
        headers = Message()

        redirected = handler.redirect_request(
            request, response, 302, "Found", headers, expected + "?canonical=1",
        )
        self.assertIsNotNone(redirected)
        self.assertEqual(redirected.full_url, expected + "?canonical=1")

        unsafe = (
            "https://evil.invalid/events/x",
            "http://api.competitivetiming.com/events/x",
            "https://api.competitivetiming.com:444/events/x",
            "https://user@api.competitivetiming.com/events/x",
            "https://user:password@api.competitivetiming.com/events/x",
            "https://api.competitivetiming.com.evil.invalid/events/x",
            "https://api.competitivetiming.com./events/x",
            "https://api%2ecompetitivetiming.com/events/x",
        )
        for target in unsafe:
            with self.subTest(target=target), self.assertRaises(server.UpstreamUnavailable) as caught:
                handler.redirect_request(request, response, 302, "PRIVATE_REDIRECT_BODY", headers, target)
            self.assertEqual(str(caught.exception), "upstream unavailable")
            self.assertNotIn("PRIVATE_REDIRECT_BODY", str(caught.exception))

    def test_final_url_is_validated_before_status_or_body_read(self):
        body = json.dumps({"status": "ok", "event": {"id": EVENT_ID}}).encode()
        path = f"/events/{EVENT_ID}"
        allowed = FakeResponse(body, final_url=server.UPSTREAM + path)
        with patch.object(server, "_open_fixed_upstream", return_value=allowed):
            self.assertEqual(server._fetch_sanitized_upstream(path)["event"]["id"], EVENT_ID)
        self.assertEqual(allowed.read_sizes, [server.MAX_UPSTREAM_BYTES + 1])

        unsafe = (
            "https://evil.invalid/private",
            "http://api.competitivetiming.com/private",
            "https://api.competitivetiming.com:444/private",
            "https://user@api.competitivetiming.com/private",
            "https://api.competitivetiming.com.evil.invalid/private",
        )
        for target in unsafe:
            response = FakeResponse(body, final_url=target, status=418)
            with self.subTest(target=target), \
                    patch.object(server, "_open_fixed_upstream", return_value=response), \
                    self.assertRaises(server.UpstreamUnavailable) as caught:
                server._fetch_sanitized_upstream(path)
            self.assertEqual(response.read_sizes, [])
            self.assertEqual(str(caught.exception), "upstream unavailable")


class CanonicalAnonymityTests(unittest.TestCase):
    def test_runner_id_only_history_masks_named_leaderboard_and_gps(self):
        bundle = minimal_bundle()
        bundle.update(
            leaderboard=[{
                "id": 7,
                "name": "PRIVATE_LEADERBOARD_NAME",
                "bib": "17",
                "last_split_index": None,
            }],
            gps_response={"positions": [{
                "runner_id": "0007",
                "runner_name": "PRIVATE_GPS_NAME",
                "bib": "18",
                "latitude": 45.0,
                "longitude": -111.0,
                "recorded_at": "2026-09-14T17:59:50Z",
            }]},
            history=[{"runner_id": "0007", "athlete_anonymous": True}],
            history_status="fresh",
            history_error=None,
            history_fetched_at=server.timestamp(NOW),
        )

        self.assertIsNone(server.validate_history_payload({"results": bundle["history"]}))
        view = server.build_event_view(bundle, NOW, include_course=False)

        self.assertEqual(len(view["runners"]), 1)
        self.assertEqual(view["runners"][0]["id"], 7)
        self.assertTrue(view["runners"][0]["is_anonymous"])
        self.assertEqual(view["runners"][0]["name"], "Anonymous Participant")
        self.assertIsNone(view["runners"][0]["bib"])
        self.assertEqual(view["positions"][0]["name"], "Anonymous Participant")
        self.assertIsNone(view["positions"][0]["bib"])
        assert_recursive_absent(self, view, "PRIVATE_LEADERBOARD_NAME", "PRIVATE_GPS_NAME")

    def test_complete_gps_response_unions_canonical_duplicates_before_cache_assignment(self):
        path = f"/gps/locations/{EVENT_ID}"
        raw = {"status": "ok", "positions": [
            {"runner_id": "0007", "runner_name": "PRIVATE_GPS_ALIAS", "bib": "17",
             "latitude": 45.0, "longitude": -111.0},
            {"runner_id": 7, "athlete_anonymous": True,
             "runner_name": "PRIVATE_GPS_ANONYMOUS_SOURCE", "bib": "18",
             "latitude": 45.1, "longitude": -111.1},
        ]}
        cache = server.JsonCache(clock=lambda: 0.0)
        response = FakeResponse(
            json.dumps(raw).encode(), final_url=server.UPSTREAM + path,
        )
        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", return_value=response):
            payload, stale = server.upstream_json(path, server.GPS_TTL_SECONDS)

        self.assertFalse(stale)
        self.assertEqual([row["runner_id"] for row in payload["positions"]], [7, 7])
        for row in payload["positions"]:
            self.assertTrue(row["is_anonymous"])
            self.assertEqual(row["runner_name"], "Anonymous Participant")
            self.assertIsNone(row["bib"])
        assert_recursive_absent(
            self, cache._entries, "PRIVATE_GPS_ALIAS", "PRIVATE_GPS_ANONYMOUS_SOURCE",
        )


class JsonCacheBoundsTests(unittest.TestCase):
    def test_bounded_cache_is_lru_and_cleans_expired_entries_and_key_locks(self):
        now = [0.0]
        cache = server.JsonCache(clock=lambda: now[0], max_entries=2)
        cache.get("a", 1, lambda: "A", max_stale_seconds=10)
        cache.get("b", 1, lambda: "B", max_stale_seconds=10)
        cache.get("a", 1, lambda: "unused", max_stale_seconds=10)
        cache.get("c", 1, lambda: "C", max_stale_seconds=10)
        self.assertEqual(list(cache._entries), ["a", "c"])
        self.assertEqual(cache._key_locks, {})

        now[0] = 11.0
        cache.get("d", 1, lambda: "D", max_stale_seconds=10)
        self.assertEqual(list(cache._entries), ["d"])
        self.assertEqual(cache._key_locks, {})

        with self.assertRaises(RuntimeError):
            cache.get("failure", 1, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(cache._key_locks, {})

    def test_unbounded_default_retains_legacy_behavior_and_dynamic_global_is_explicitly_bounded(self):
        legacy = server.JsonCache(clock=lambda: 0.0)
        for index in range(server.MAX_DYNAMIC_CACHE_ENTRIES + 1):
            legacy.get(str(index), 10, lambda index=index: index)
        self.assertEqual(len(legacy._entries), server.MAX_DYNAMIC_CACHE_ENTRIES + 1)
        self.assertIsNone(legacy._max_entries)
        self.assertEqual(server.CACHE._max_entries, server.MAX_DYNAMIC_CACHE_ENTRIES)
        self.assertGreater(server.MAX_DYNAMIC_CACHE_ENTRIES, 0)

    def test_unrelated_keys_load_concurrently(self):
        cache = server.JsonCache(max_entries=4)
        started = {key: threading.Event() for key in ("event-a", "event-b")}
        release = threading.Event()
        outcomes = []

        def load(key):
            started[key].set()
            release.wait(2)
            return key

        workers = [
            threading.Thread(target=lambda key=key: outcomes.append(cache.get(key, 30, lambda: load(key))[0]))
            for key in started
        ]
        for worker in workers:
            worker.start()
        both_started = all(event.wait(1) for event in started.values())
        release.set()
        for worker in workers:
            worker.join(2)

        self.assertTrue(both_started)
        self.assertEqual(set(outcomes), set(started))
        self.assertFalse(any(worker.is_alive() for worker in workers))


class LeaderboardWorkBoundsTests(unittest.TestCase):
    def test_one_record_pages_stop_at_500_requests(self):
        calls = 0

        def fetch(_path):
            nonlocal calls
            calls += 1
            if calls > 500:
                raise AssertionError("page budget exceeded")
            return {"leaderboard": [{"id": calls}], "has_more": True}, False

        rows, incomplete = server._load_leaderboard_pages(EVENT_ID, fetch)
        self.assertTrue(incomplete)
        self.assertEqual(calls, server.MAX_LEADERBOARD_PAGES)
        self.assertLessEqual(calls, 500)
        self.assertEqual(len(rows), calls)

    def test_unique_and_cumulative_row_budgets_fail_without_partial_page_processing(self):
        pages = iter([
            ({"leaderboard": [{"id": 1}, {"id": 2}], "has_more": True}, False),
            ({"leaderboard": [{"id": 3}, {"id": 4}], "has_more": True}, False),
        ])
        with patch.object(server, "MAX_LEADERBOARD_UNIQUE_RESULTS", 3), \
                patch.object(server, "MAX_LEADERBOARD_RESPONSE_ROWS", 3):
            rows, incomplete = server._load_leaderboard_pages(EVENT_ID, lambda _path: next(pages))
        self.assertTrue(incomplete)
        self.assertEqual([row["id"] for row in rows], [1, 2])

    def test_elapsed_budget_and_incremental_identity_tracking(self):
        clock_values = iter([0.0, 0.0, 0.0, server.MAX_LEADERBOARD_LOAD_SECONDS + 1])
        pages = iter([
            ({"leaderboard": [{"id": 1}], "has_more": True}, False),
            ({"leaderboard": [{"id": 2}], "has_more": False}, False),
        ])
        with patch.object(server, "deduplicate_records", side_effect=AssertionError("quadratic rebuild")):
            rows, incomplete = server._load_leaderboard_pages(
                EVENT_ID, lambda _path: next(pages), clock=lambda: next(clock_values),
            )
        self.assertTrue(incomplete)
        self.assertEqual([row["id"] for row in rows], [1])

    def test_incomplete_dynamic_load_never_populates_shared_cache(self):
        cache = server.JsonCache(max_entries=8, clock=lambda: 0.0)
        calls = 0

        def fetch(_path):
            nonlocal calls
            calls += 1
            return {"leaderboard": [{"id": calls}], "has_more": True}

        with patch.object(server, "CACHE", cache), \
                patch.object(server, "MAX_LEADERBOARD_PAGES", 2), \
                patch.object(server, "_fetch_sanitized_upstream", side_effect=fetch), \
                self.assertRaises(server.CacheUnavailable):
            server.load_leaderboard(
                EVENT_ID, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            )
        self.assertNotIn(server._leaderboard_cache_key(EVENT_ID), cache._entries)

    def test_malformed_has_more_never_caches_or_publishes_partial_identity(self):
        partial_name = "PRIVATE_PARTIAL_LEADERBOARD_IDENTITY_SENTINEL"
        later_name = "PRIVATE_UNFETCHED_ANONYMITY_SENTINEL"
        for malformed in ("true", 1, None, [], {}):
            with self.subTest(has_more=malformed):
                cache = server.JsonCache(max_entries=8, clock=lambda: 0.0)
                pages = [
                    {"leaderboard": [{"id": 7, "name": partial_name}],
                     "has_more": malformed},
                    {"leaderboard": [{"id": 7, "is_anonymous": True,
                                      "name": later_name}],
                     "has_more": False},
                ]
                calls = []

                def fetch(_path):
                    calls.append(len(calls))
                    return pages[calls[-1]]

                history_bundle = {
                    "history": [], "history_status": "unavailable",
                    "history_error": "history: unavailable", "history_fetched_at": None,
                }
                with patch.object(server, "CACHE", cache), \
                        patch.object(server, "_fetch_sanitized_upstream", side_effect=fetch), \
                        patch.object(server, "upstream_json", return_value=({}, False)):
                    bundle = server.load_event_bundle(
                        EVENT_ID, history_bundle, dynamic=True,
                    )
                view = server.build_event_view(bundle, NOW, include_course=False)

                self.assertEqual(calls, [0])
                self.assertEqual(bundle["leaderboard"], [])
                self.assertEqual(bundle["errors"], ["leaderboard: unavailable"])
                self.assertEqual(view["runners"], [])
                self.assertEqual(view["positions"], [])
                self.assertNotIn(server._leaderboard_cache_key(EVENT_ID), cache._entries)
                assert_recursive_absent(
                    self, {"cache": cache._entries, "bundle": bundle, "view": view},
                    partial_name, later_name,
                )

    def test_malformed_has_more_preserves_safe_stale_leaderboard_union(self):
        now = [0.0]
        cache = server.JsonCache(max_entries=8, clock=lambda: now[0])
        key = server._leaderboard_cache_key(EVENT_ID)
        safe = [{"id": 7, "is_anonymous": True,
                 "name": "Anonymous Participant", "bib": None}]
        cache.get(
            key, server.LEADERBOARD_TTL_SECONDS, lambda: safe,
            max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
        )
        now[0] = server.LEADERBOARD_TTL_SECONDS

        with patch.object(server, "CACHE", cache), patch.object(
                server, "_fetch_sanitized_upstream",
                return_value={"leaderboard": [{
                    "id": 7, "name": "PRIVATE_PARTIAL_REFRESH_IDENTITY_SENTINEL",
                }], "has_more": "true"}):
            rows, stale = server.load_leaderboard(
                EVENT_ID,
                max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            )

        self.assertTrue(stale)
        self.assertEqual(rows, safe)
        self.assertIs(cache._entries[key].value, safe)
        assert_recursive_absent(
            self, {"rows": rows, "cache": cache._entries},
            "PRIVATE_PARTIAL_REFRESH_IDENTITY_SENTINEL",
        )


class LeaderboardCountValidationTests(unittest.TestCase):
    def test_count_accepts_only_exact_bounded_literal_integers(self):
        valid_row = {"id": 7, "name": "Runner"}
        invalid = (
            ("true", [valid_row], True),
            ("false", [], False),
            ("float", [valid_row], 1.0),
            ("string", [valid_row], "1"),
            ("negative", [], -1),
            ("oversized", [valid_row], server.LEADERBOARD_PAGE_SIZE + 1),
            ("zero-with-nonempty", [valid_row], 0),
            ("nonzero-with-empty", [], 1),
            ("mismatched", [valid_row], 2),
        )
        for label, rows, count in invalid:
            payload = {"leaderboard": rows, "count": count, "has_more": False}
            with self.subTest(label=label), self.assertRaises(RuntimeError):
                server.sanitize_leaderboard_payload(payload)

    def test_exact_and_absent_counts_preserve_bounded_pagination(self):
        exact_pages = iter([
            ({"leaderboard": [{"id": 1}], "count": 1, "has_more": True}, False),
            ({"leaderboard": [{"id": 2}], "count": 1, "has_more": False}, False),
        ])
        rows, incomplete = server._load_leaderboard_pages(
            EVENT_ID, lambda _path: next(exact_pages),
        )
        self.assertFalse(incomplete)
        self.assertEqual([row["id"] for row in rows], [1, 2])

        absent_pages = iter([
            ({"leaderboard": [{"id": 1}], "has_more": True}, False),
            ({"leaderboard": [{"id": 2}], "has_more": False}, False),
        ])
        rows, incomplete = server._load_leaderboard_pages(
            EVENT_ID, lambda _path: next(absent_pages),
        )
        self.assertFalse(incomplete)
        self.assertEqual([row["id"] for row in rows], [1, 2])

        rows, incomplete = server._load_leaderboard_pages(
            EVENT_ID,
            lambda _path: ({"leaderboard": [], "count": 0, "has_more": False}, False),
        )
        self.assertFalse(incomplete)
        self.assertEqual(rows, [])

    def test_bad_count_is_incomplete_before_current_page_identity_admission(self):
        sentinel_name = "PRIVATE_BAD_COUNT_PAGE_NAME_SENTINEL"
        sentinel_bib = "PRIVATE_BAD_COUNT_PAGE_BIB_SENTINEL"
        invalid = (
            ("true", [{"id": 7, "name": sentinel_name, "bib": sentinel_bib}], True),
            ("false", [], False),
            ("float", [{"id": 7, "name": sentinel_name, "bib": sentinel_bib}], 1.0),
            ("string", [{"id": 7, "name": sentinel_name, "bib": sentinel_bib}], "1"),
            ("negative", [], -1),
            ("oversized", [{"id": 7, "name": sentinel_name, "bib": sentinel_bib}],
             server.LEADERBOARD_PAGE_SIZE + 1),
            ("zero-with-nonempty", [{"id": 7, "name": sentinel_name, "bib": sentinel_bib}], 0),
            ("nonzero-with-empty", [], 1),
            ("mismatched", [{"id": 7, "name": sentinel_name, "bib": sentinel_bib}], 2),
        )
        for label, batch, count in invalid:
            with self.subTest(label=label):
                rows, incomplete = server._load_leaderboard_pages(
                    EVENT_ID,
                    lambda _path, batch=batch, count=count: ({
                        "leaderboard": batch, "count": count, "has_more": False,
                    }, False),
                )
                self.assertTrue(incomplete)
                self.assertEqual(rows, [])
                assert_recursive_absent(self, rows, sentinel_name, sentinel_bib)

    def test_bad_count_cold_load_returns_generic_unavailable_without_cache_or_identity(self):
        sentinel_name = "PRIVATE_BAD_COUNT_COLD_NAME_SENTINEL"
        sentinel_bib = "PRIVATE_BAD_COUNT_COLD_BIB_SENTINEL"
        cache = server.JsonCache(max_entries=8, clock=lambda: 0.0)
        history_bundle = {
            "history": [], "history_status": "unavailable",
            "history_error": "history: unavailable", "history_fetched_at": None,
        }
        malformed = {
            "leaderboard": [{"id": 7, "name": sentinel_name, "bib": sentinel_bib}],
            "count": 2,
            "has_more": False,
        }

        with patch.object(server, "CACHE", cache), \
                patch.object(server, "_fetch_sanitized_upstream", return_value=malformed), \
                patch.object(server, "upstream_json", return_value=({}, False)):
            bundle = server.load_event_bundle(EVENT_ID, history_bundle, dynamic=True)
        view = server.build_event_view(bundle, NOW, include_course=False)

        self.assertEqual(bundle["leaderboard"], [])
        self.assertEqual(bundle["errors"], ["leaderboard: unavailable"])
        self.assertEqual(view["runners"], [])
        self.assertEqual(view["positions"], [])
        self.assertNotIn(server._leaderboard_cache_key(EVENT_ID), cache._entries)
        assert_recursive_absent(
            self, {"bundle": bundle, "view": view, "cache": cache._entries},
            sentinel_name, sentinel_bib,
        )

    def test_bad_count_preserves_complete_stale_anonymity_union_and_labels_it_stale(self):
        now = [0.0]
        cache = server.JsonCache(max_entries=8, clock=lambda: now[0])
        valid_pages = iter([
            {"leaderboard": [{
                "id": 7, "name": "PRIVATE_OLD_PUBLIC_NAME_SENTINEL", "bib": "17",
            }], "count": 1, "has_more": True},
            {"leaderboard": [{
                "runner_id": "0007", "athlete_anonymous": True,
                "name": "PRIVATE_OLD_ANONYMOUS_NAME_SENTINEL", "bib": "18",
            }], "count": 1, "has_more": False},
        ])
        with patch.object(server, "CACHE", cache), patch.object(
                server, "_fetch_sanitized_upstream", side_effect=lambda _path: next(valid_pages)):
            safe, stale = server.load_leaderboard(
                EVENT_ID,
                max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            )
        self.assertFalse(stale)
        self.assertEqual(len(safe), 1)
        self.assertEqual(safe[0]["id"], 7)
        self.assertTrue(safe[0]["is_anonymous"])
        self.assertEqual(safe[0]["name"], "Anonymous Participant")
        self.assertIsNone(safe[0]["bib"])

        now[0] = server.LEADERBOARD_TTL_SECONDS
        current_name = "PRIVATE_BAD_COUNT_REFRESH_NAME_SENTINEL"
        current_bib = "PRIVATE_BAD_COUNT_REFRESH_BIB_SENTINEL"
        malformed = {
            "leaderboard": [{"id": 7, "name": current_name, "bib": current_bib}],
            "count": 2,
            "has_more": False,
        }
        history_bundle = {
            "history": [], "history_status": "unavailable",
            "history_error": "history: unavailable", "history_fetched_at": None,
        }
        with patch.object(server, "CACHE", cache), \
                patch.object(server, "_fetch_sanitized_upstream", return_value=malformed), \
                patch.object(server, "upstream_json", return_value=({}, False)):
            bundle = server.load_event_bundle(EVENT_ID, history_bundle, dynamic=True)
        view = server.build_event_view(bundle, NOW, include_course=False)
        key = server._leaderboard_cache_key(EVENT_ID)

        self.assertEqual(bundle["leaderboard"], [{**safe[0], "chip_start_seconds": None}])
        self.assertEqual(bundle["errors"], ["leaderboard: stale or incomplete"])
        self.assertTrue(bundle["source_freshness"]["leaderboard"]["stale"])
        self.assertEqual(
            bundle["source_freshness"]["leaderboard"]["error"],
            "leaderboard: stale or incomplete",
        )
        self.assertTrue(view["upstream_stale"])
        self.assertEqual(view["runners"][0]["name"], "Anonymous Participant")
        self.assertIsNone(view["runners"][0]["bib"])
        self.assertIs(cache._entries[key].value, safe)
        assert_recursive_absent(
            self, {"bundle": bundle, "view": view, "cache": cache._entries},
            current_name, current_bib,
        )


class HistoryPaginationValidationTests(unittest.TestCase):
    def test_history_has_more_accepts_only_absent_or_literal_booleans(self):
        complete = {"results": [{"id": 7}]}
        self.assertIsNone(server.validate_history_payload(complete))
        self.assertIsNone(server.validate_history_payload({**complete, "has_more": False}))
        self.assertEqual(
            server.validate_history_payload({**complete, "has_more": True}),
            "history: incomplete",
        )
        for malformed in ("true", 1, None, [], {}):
            with self.subTest(has_more=malformed):
                self.assertEqual(
                    server.validate_history_payload({**complete, "has_more": malformed}),
                    "history: malformed",
                )

    def test_malformed_history_has_more_is_not_cached_or_exposed(self):
        path = f"/events/{EVENT_ID}/results"
        partial_name = "PRIVATE_PARTIAL_HISTORY_IDENTITY_SENTINEL"
        later_name = "PRIVATE_UNFETCHED_HISTORY_ANONYMITY_SENTINEL"
        for malformed in ("true", 1, None, [], {}):
            with self.subTest(has_more=malformed):
                cache = server.JsonCache(max_entries=8, clock=lambda: 0.0)
                pages = [
                    {"status": "ok", "results": [{"id": 7, "name": partial_name}],
                     "has_more": malformed},
                    {"status": "ok", "results": [{
                        "id": 7, "is_anonymous": True, "name": later_name,
                    }], "has_more": False},
                ]
                responses = [
                    FakeResponse(json.dumps(page).encode(), final_url=server.UPSTREAM + path)
                    for page in pages
                ]

                with patch.object(server, "CACHE", cache), patch.object(
                        server.urllib.request, "urlopen", side_effect=responses) as opened:
                    history = server.load_checkpoint_history(
                        EVENT_ID,
                        max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
                    )

                self.assertEqual(opened.call_count, 1)
                self.assertEqual(history["history"], [])
                self.assertEqual(history["history_status"], "unavailable")
                self.assertEqual(history["history_error"], "history: unavailable")
                self.assertNotIn(server.UPSTREAM + path, cache._entries)
                assert_recursive_absent(
                    self, {"history": history, "cache": cache._entries},
                    partial_name, later_name,
                )

    def test_incomplete_history_is_not_cached_or_used_as_identity_evidence(self):
        path = f"/events/{EVENT_ID}/results"
        partial_name = "PRIVATE_INCOMPLETE_HISTORY_IDENTITY_SENTINEL"
        raw = {
            "status": "ok",
            "results": [{
                "id": 7,
                "name": partial_name,
                "bib": "PRIVATE_INCOMPLETE_HISTORY_BIB_SENTINEL",
                "athlete_anonymous": True,
            }],
            "has_more": True,
        }
        cache = server.JsonCache(max_entries=8, clock=lambda: 0.0)
        response = FakeResponse(
            json.dumps(raw).encode(), final_url=server.UPSTREAM + path,
        )

        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", return_value=response):
            history = server.load_checkpoint_history(
                EVENT_ID,
                max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            )

        bundle = minimal_bundle()
        bundle.update(
            leaderboard=[{"id": 7, "name": "Public Runner", "bib": "17"}],
            **history,
        )
        view = server.build_event_view(bundle, NOW, include_course=False)

        self.assertEqual(history["history"], [])
        self.assertEqual(history["history_status"], "unavailable")
        self.assertNotIn(server.UPSTREAM + path, cache._entries)
        self.assertEqual(view["runners"][0]["name"], "Public Runner")
        self.assertEqual(view["runners"][0]["bib"], "17")
        self.assertFalse(view["runners"][0]["is_anonymous"])
        assert_recursive_absent(
            self, {"history": history, "cache": cache._entries, "view": view},
            partial_name, "PRIVATE_INCOMPLETE_HISTORY_BIB_SENTINEL",
        )


class CourseBoundsAndCapabilityTests(unittest.TestCase):
    def test_course_sanitizer_rejects_oversized_raw_maps_and_point_arrays_without_truncation(self):
        point = {"lat": 45.0, "lng": -111.0, "ele": 1000}
        cases = (
            {"courseMaps": [{}] * (server.MAX_COURSE_MAPS + 1)},
            {"courseMaps": [{"eventId": EVENT_ID,
                             "trackPoints": [point] * (server.MAX_COURSE_TRACK_POINTS + 1),
                             "splitPoints": []}]},
            {"courseMaps": [{"eventId": EVENT_ID, "trackPoints": [point, point],
                             "splitPoints": [point] * (server.MAX_COURSE_SPLIT_POINTS + 1)}]},
        )
        for payload in cases:
            with self.subTest(size=len(payload["courseMaps"])), self.assertRaises(RuntimeError):
                server.sanitize_course_payload(payload)

    def test_builder_marks_oversized_unsanitized_geometry_unavailable_instead_of_connecting_prefix(self):
        point = {"lat": 45.0, "lng": -111.0, "ele": 1000}
        oversized = {
            "eventId": EVENT_ID,
            "trackPoints": [point] * (server.MAX_COURSE_TRACK_POINTS + 1),
            "splitPoints": [point, point],
        }
        view = server.build_event_view(minimal_bundle(maps=[oversized]), NOW, include_course=True)
        self.assertFalse(view["capabilities"]["course_map"])
        self.assertFalse(view["capabilities"]["elevation"])
        self.assertEqual(view["course"]["track_points"], [])
        self.assertEqual(view["course"]["split_points"], [])

    def test_only_literal_true_enables_provider_live_capability(self):
        for malformed in ("true", "1", 1, 1.0, [], {}):
            with self.subTest(value=malformed):
                view = server.build_event_view(
                    minimal_bundle(live_tracking_enabled=malformed), NOW, include_course=False,
                )
                self.assertFalse(view["capabilities"]["live_tracking"])

        enabled = server.build_event_view(
            minimal_bundle(live_tracking_enabled=True), NOW, include_course=False,
        )
        self.assertTrue(enabled["capabilities"]["live_tracking"])


if __name__ == "__main__":
    unittest.main()
