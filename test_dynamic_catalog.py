import datetime as dt
import io
import json
import re
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import competitive_catalog as catalog
import server
from api.index import response_for


NOW = dt.datetime(2026, 9, 14, 18, 0, tzinfo=dt.timezone.utc)


def raw_race(slug, name, race_type="road", year=2026, event_id=None, **changes):
    event_id = event_id or f"{slug}-5k-{year}"
    race = {
        "id": slug,
        "slug": slug,
        "name": name,
        "location": "Bozeman, MT",
        "timezone": "America/Denver",
        "race_type": race_type,
        "drive_folder_id": "private-drive-id",
        "registration_url": "https://registration.invalid/private",
        "events": [{
            "id": event_id,
            "race_id": slug,
            "name": f"{name} 5K",
            "display_name": "5K",
            "event_date": f"{year}-09-14T12:00:00.000Z",
            "start_time": "08:00:00",
            "estimated_start_time": "08:15:00",
            "course_status": "closed",
            "split_names": ["Start", "Half", "Finish"],
            "split_count": 3,
            "course_distance_miles": 3.1,
            "live_tracking_enabled": True,
            "custom_field_settings": {"secret": "event-private"},
            "import_source_url": "https://private.invalid/source",
        }],
    }
    race.update(changes)
    return race


def catalog_payload(*races):
    return {"status": "ok", "races": list(races)}


def selected_catalog(event_id="alpha-run-5k-2026"):
    return tuple(catalog.sanitize_catalog(catalog_payload(
        raw_race("alpha-run", "Alpha Run", event_id=event_id)
    )))


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def assert_recursive_absent(testcase, value, *sentinels):
    """Probe cache keys and values without assuming a JSON-only container."""
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


class CatalogPureLayerTests(unittest.TestCase):
    def test_filters_to_exact_foot_race_types_and_minimizes_every_row(self):
        raw = catalog_payload(
            raw_race("road-race", "Road Race", "road"),
            raw_race("xc-race", "XC Race", "cross_country"),
            raw_race("trail-race", "Trail Race", "trail_running"),
            raw_race("bike-race", "Bike Race", "cycling"),
        )
        rows = catalog.sanitize_catalog(raw)
        self.assertEqual({row["race_type"] for row in rows}, catalog.FOOT_RACE_TYPES)
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows[0]), catalog.RACE_PUBLIC_FIELDS)
        self.assertEqual(set(rows[0]["events"][0]), catalog.EVENT_PUBLIC_FIELDS)
        encoded = json.dumps(rows)
        for forbidden in ("drive_folder", "registration_url", "custom_field", "import_source", "private-drive-id", "event-private"):
            self.assertNotIn(forbidden, encoded)

    def test_rejects_invalid_or_cross_race_embedded_identifiers(self):
        valid = raw_race("safe-race", "Safe Race")
        valid["events"].extend([
            {**valid["events"][0], "id": "../escape"},
            {**valid["events"][0], "id": "other-event", "race_id": "other-race"},
        ])
        rows = catalog.sanitize_catalog(catalog_payload(
            valid,
            raw_race("bad%2fslug", "Bad slug"),
        ))
        self.assertEqual([event["id"] for event in rows[0]["events"]], ["safe-race-5k-2026"])
        self.assertEqual(len(rows), 1)

    def test_road_catalog_excludes_non_foot_structures_and_tokens_but_keeps_run_walk(self):
        soapbox = raw_race("septemberfest-soapbox-derby", "Septemberfest Soapbox Derby")
        soapbox["events"][0]["derby_config"] = {"lanes": 2}
        mixed = raw_race("missoula-marathon", "Missoula Marathon", event_id="missoula-marathon-2026")
        mixed["events"].extend([
            {**mixed["events"][0], "id": "missoula-marathon-handcycle-marathon-2026", "name": "Handcycle - Marathon"},
            {**mixed["events"][0], "id": "missoula-run-walk-5k-2026", "name": "Run/Walk 5K"},
        ])
        rows = catalog.sanitize_catalog(catalog_payload(
            soapbox,
            raw_race("community-bike-race", "Community Bike Race"),
            mixed,
        ))
        self.assertEqual([row["slug"] for row in rows], ["missoula-marathon"])
        self.assertEqual(
            [event["id"] for event in rows[0]["events"]],
            ["missoula-run-walk-5k-2026", "missoula-marathon-2026"],
        )

    def test_mixed_sport_series_keeps_foot_events_when_series_name_mentions_bikes(self):
        mixed = raw_race(
            "bike-and-run-weekend", "Bike and Run Weekend", event_id="community-5k-2026"
        )
        mixed["events"][0]["name"] = "Community 5K Run"
        mixed["events"].append({
            **mixed["events"][0],
            "id": "community-handcycle-2026",
            "name": "Community Handcycle",
        })

        rows = catalog.sanitize_catalog(catalog_payload(mixed))

        self.assertEqual([row["slug"] for row in rows], ["bike-and-run-weekend"])
        self.assertEqual([event["id"] for event in rows[0]["events"]], ["community-5k-2026"])

    def test_search_ranks_exact_prefix_then_substring_and_filters_year(self):
        rows = catalog.sanitize_catalog(catalog_payload(
            raw_race("the-alpha-run", "The Alpha Run", year=2024),
            raw_race("alpha-ridge", "Alpha Ridge", year=2025),
            raw_race("alpha", "Alpha", year=2026),
            raw_race("other", "Other", year=2026, location="Alpha, MT"),
        ))
        result = catalog.search_catalog(rows, "alpha", limit=99)
        self.assertEqual([row["slug"] for row in result], ["alpha", "alpha-ridge", "other", "the-alpha-run"])
        limited = catalog.search_catalog(rows, "alpha", limit=2)
        self.assertEqual([row["slug"] for row in limited], ["alpha", "alpha-ridge"])
        year = catalog.search_catalog(rows, "alpha", year=2025, limit=99)
        self.assertEqual([row["slug"] for row in year], ["alpha-ridge"])
        self.assertEqual({event["event_date"][:4] for event in year[0]["events"]}, {"2025"})

    def test_search_requires_a_nonblank_bounded_query_and_caps_limit(self):
        rows = selected_catalog()
        for query in ("", " ", "x", "x" * (catalog.MAX_QUERY_LENGTH + 1)):
            with self.assertRaises(catalog.CatalogQueryError):
                catalog.search_catalog(rows, query)
        result = catalog.search_catalog(rows * (catalog.MAX_SEARCH_RESULTS + 5), "alpha", limit=10_000)
        self.assertLessEqual(len(result), catalog.MAX_SEARCH_RESULTS)
        self.assertEqual(catalog.MAX_SEARCH_RESULTS, 30)

    def test_one_character_query_is_rejected_before_catalog_lookup(self):
        cache = Mock()
        with self.assertRaises(catalog.CatalogQueryError):
            server.build_catalog_payload("x", catalog_cache=cache)
        cache.get.assert_not_called()

    def test_cache_instances_isolate_injected_loaders_and_cache_only_minimized_rows(self):
        calls = [0, 0]

        def first():
            calls[0] += 1
            return catalog_payload(raw_race("first", "First"))

        def second():
            calls[1] += 1
            return catalog_payload(raw_race("second", "Second"))

        one = catalog.CatalogCache(loader=first, ttl_seconds=60, clock=lambda: 10)
        two = catalog.CatalogCache(loader=second, ttl_seconds=60, clock=lambda: 10)
        self.assertIs(one.get(), one.get())
        self.assertEqual(calls, [1, 0])
        self.assertEqual(two.get()[0]["slug"], "second")
        self.assertEqual(calls, [1, 1])
        self.assertNotIn("drive_folder_id", json.dumps(one.get()))

    def test_catalog_cache_returns_only_minimized_stale_data_and_labels_payload(self):
        now = [0.0]
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("private transient failure")
            return catalog_payload(raw_race("cached", "Cached Race"))

        cache = catalog.CatalogCache(loader=loader, ttl_seconds=60, clock=lambda: now[0])
        first = cache.get()
        now[0] = 61.0
        second = cache.get()
        payload = server.build_catalog_payload("cached", catalog_cache=cache)

        self.assertIs(first, second)
        self.assertTrue(cache.stale)
        self.assertTrue(payload["stale"])
        self.assertNotIn("private", json.dumps(payload).lower())
        self.assertNotIn("drive_folder_id", json.dumps(second))

    def test_catalog_stale_boundary_is_inclusive_then_fails_generically(self):
        now = [0.0]
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("PRIVATE_CATALOG_FAILURE_SENTINEL")
            return catalog_payload(raw_race("bounded", "Bounded Race"))

        cache = catalog.CatalogCache(
            loader=loader,
            ttl_seconds=60,
            max_stale_seconds=catalog.CATALOG_MAX_STALE_SECONDS,
            clock=lambda: now[0],
        )
        fresh = cache.get()
        now[0] = catalog.CATALOG_MAX_STALE_SECONDS
        self.assertIs(cache.get(), fresh)
        self.assertTrue(cache.stale)

        now[0] += 1
        with self.assertRaises(catalog.CatalogUnavailable) as caught:
            cache.get()
        self.assertEqual(str(caught.exception), "catalog unavailable")
        self.assertNotIn("PRIVATE_CATALOG_FAILURE_SENTINEL", str(caught.exception))

    def test_json_cache_bounded_policy_preserves_legacy_unbounded_default(self):
        now = [0.0]
        bounded = server.JsonCache(clock=lambda: now[0])
        fresh, stale = bounded.get("dynamic", 30, lambda: {"value": "fresh"},
                                   max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS)
        self.assertFalse(stale)

        now[0] = server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
        value, stale = bounded.get(
            "dynamic", 30, lambda: (_ for _ in ()).throw(OSError("PRIVATE_CACHE_FAILURE_SENTINEL")),
            max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
        )
        self.assertIs(value, fresh)
        self.assertTrue(stale)

        now[0] += 1
        with self.assertRaises(server.CacheUnavailable) as caught:
            bounded.get(
                "dynamic", 30, lambda: (_ for _ in ()).throw(OSError("PRIVATE_CACHE_FAILURE_SENTINEL")),
                max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            )
        self.assertEqual(str(caught.exception), "cached source unavailable")

        legacy = server.JsonCache(clock=lambda: now[0])
        legacy.get("legacy", 30, lambda: {"legacy": True})
        now[0] += server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS * 100
        value, stale = legacy.get("legacy", 30, lambda: (_ for _ in ()).throw(OSError("offline")))
        self.assertEqual(value, {"legacy": True})
        self.assertTrue(stale)

    def test_raw_result_response_is_minimized_before_shared_memory_cache(self):
        raw = {
            "status": "ok",
            "total": 1,
            "results": [{
                "id": 7,
                "event_id": "alpha-run-5k-2026",
                "name": "Runner",
                "bib": "17",
                "status": "FINISHED",
                "finish_time_seconds": 1500,
                "overall_place": 1,
                "chip_start_seconds": 3,
                "last_split_index": 1,
                "last_split_time": 1500,
                "email": "runner-private@example.com",
                "custom_answers": {"secret": "answer-secret"},
                "splits": [{
                    "split_index": 1,
                    "elapsed_seconds": 1500,
                    "cumulative_place": 1,
                    "segment_place": 2,
                    "private_video_url": "https://private.invalid/video",
                }],
            }],
        }
        body = json.dumps(raw).encode()
        cache = server.JsonCache()
        response = FakeResponse(body)
        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", return_value=response) as fetch:
            payload, stale = server.upstream_json(
                "/events/alpha-run-5k-2026/results", server.HISTORY_TTL_SECONDS
            )

        cached = cache._entries[server.UPSTREAM + "/events/alpha-run-5k-2026/results"].value
        encoded = json.dumps(cached)
        self.assertFalse(stale)
        self.assertEqual(payload, cached)
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn("runner-private@example.com", encoded)
        self.assertNotIn("answer-secret", encoded)
        self.assertNotIn("private_video_url", encoded)
        self.assertEqual(cached["results"][0]["splits"][0], {
            "split_index": 1,
            "elapsed_seconds": 1500,
            "cumulative_place": 1,
            "segment_place": 2,
        })

    def test_result_anonymity_unions_across_duplicate_id_forms_before_cache_assignment(self):
        event_id = "alpha-run-5k-2026"
        path = f"/events/{event_id}/results"
        cases = (
            [
                {"id": 7, "name": "PRIVATE_RESULT_NAMED_FIRST_SENTINEL", "bib": "17",
                 "unknown": {"deep": ["PRIVATE_RESULT_RECURSIVE_SENTINEL"]}},
                {"id": "0007", "athlete_anonymous": True,
                 "name": "PRIVATE_RESULT_ANONYMOUS_SECOND_SENTINEL", "bib": "18"},
            ],
            [
                {"id": "0007", "is_anonymous": True,
                 "name": "PRIVATE_RESULT_ANONYMOUS_FIRST_SENTINEL", "bib": "18"},
                {"id": 7, "name": "PRIVATE_RESULT_NAMED_SECOND_SENTINEL", "bib": "17",
                 "unknown": {"deep": ["PRIVATE_RESULT_RECURSIVE_SENTINEL"]}},
            ],
        )
        sentinels = (
            "PRIVATE_RESULT_NAMED_FIRST_SENTINEL",
            "PRIVATE_RESULT_ANONYMOUS_SECOND_SENTINEL",
            "PRIVATE_RESULT_ANONYMOUS_FIRST_SENTINEL",
            "PRIVATE_RESULT_NAMED_SECOND_SENTINEL",
            "PRIVATE_RESULT_RECURSIVE_SENTINEL",
        )

        for rows in cases:
            with self.subTest(order=[row.get("is_anonymous") or row.get("athlete_anonymous")
                                     for row in rows]):
                cache = server.JsonCache(clock=lambda: 0.0)
                raw = {"status": "ok", "total": 2, "results": rows}
                with patch.object(server, "CACHE", cache), patch.object(
                        server.urllib.request, "urlopen",
                        return_value=FakeResponse(json.dumps(raw).encode())):
                    history = server.load_checkpoint_history(event_id)

                cached = cache._entries[server.UPSTREAM + path].value
                self.assertIs(history["history"][0], cached["results"][0])
                self.assertEqual([row["id"] for row in cached["results"]], [7, 7])
                self.assertEqual(len(history["history"]), 2)
                for row in cached["results"]:
                    self.assertTrue(row["is_anonymous"])
                    self.assertEqual(row.get("name"), "Anonymous Participant")
                    self.assertIsNone(row.get("bib"))
                assert_recursive_absent(self, cache._entries, *sentinels)
                assert_recursive_absent(self, history, *sentinels)

    def test_result_cache_stale_fallback_is_minimized_and_age_bounded(self):
        event_id = "alpha-run-5k-2026"
        path = f"/events/{event_id}/results"
        raw = {
            "status": "ok", "total": 1,
            "results": [{
                "id": 7, "event_id": event_id, "name": "Runner", "last_split_index": 1,
                "last_split_time": 700, "chip_start_seconds": 0,
                "email": "PRIVATE_RESULT_EMAIL_SENTINEL",
                "custom_answers": {"nested": ["PRIVATE_RESULT_CUSTOM_SENTINEL"]},
                "unknown": {"deep": {"value": "PRIVATE_RESULT_UNKNOWN_SENTINEL"}},
                "splits": [{"split_index": 0, "elapsed_seconds": 0},
                           {"split_index": 1, "elapsed_seconds": 700,
                            "unknown": {"value": "PRIVATE_RESULT_SPLIT_SENTINEL"}}],
            }],
        }
        now = [0.0]
        cache = server.JsonCache(clock=lambda: now[0])
        failures = [FakeResponse(json.dumps(raw).encode()),
                    OSError("PRIVATE_RESULT_FAILURE_SENTINEL"),
                    OSError("PRIVATE_RESULT_FAILURE_SENTINEL")]
        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", side_effect=failures):
            fresh = server.load_checkpoint_history(
                event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            )
            now[0] = server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            stale = server.load_checkpoint_history(
                event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            )
            now[0] += 1
            expired = server.load_checkpoint_history(
                event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            )

        cached = cache._entries[server.UPSTREAM + path].value
        encoded = json.dumps({"cached": cached, "fresh": fresh, "stale": stale, "expired": expired})
        self.assertEqual(fresh["history_status"], "fresh")
        self.assertEqual(stale["history_status"], "stale")
        self.assertEqual(expired["history_status"], "unavailable")
        self.assertEqual(expired["history"], [])
        for sentinel in ("PRIVATE_RESULT_EMAIL_SENTINEL", "PRIVATE_RESULT_CUSTOM_SENTINEL",
                         "PRIVATE_RESULT_UNKNOWN_SENTINEL", "PRIVATE_RESULT_SPLIT_SENTINEL",
                         "PRIVATE_RESULT_FAILURE_SENTINEL"):
            self.assertNotIn(sentinel, encoded)

    def test_leaderboard_cache_and_returned_rows_are_recursively_minimized_and_bounded(self):
        event_id = "alpha-run-5k-2026"
        path = f"/events/{event_id}/leaderboard?limit=500&offset=0"
        raw = {
            "status": "ok", "total": 1, "has_more": False,
            "leaderboard": [{
                "id": 7, "name": "PRIVATE_ANONYMOUS_NAME_SENTINEL", "bib": "17",
                "is_anonymous": True, "last_split_index": 1, "last_split_time": 700,
                "chip_start_seconds": 0, "email": "PRIVATE_LEADERBOARD_EMAIL_SENTINEL",
                "custom_answers": {"nested": ["PRIVATE_LEADERBOARD_CUSTOM_SENTINEL"]},
                "unknown": {"deep": {"value": "PRIVATE_LEADERBOARD_UNKNOWN_SENTINEL"}},
            }],
        }
        now = [0.0]
        cache = server.JsonCache(clock=lambda: now[0])
        failures = [FakeResponse(json.dumps(raw).encode()),
                    OSError("PRIVATE_LEADERBOARD_FAILURE_SENTINEL"),
                    OSError("PRIVATE_LEADERBOARD_FAILURE_SENTINEL")]
        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", side_effect=failures):
            fresh, was_stale = server.load_leaderboard(
                event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            )
            self.assertFalse(was_stale)
            now[0] = server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            stale, was_stale = server.load_leaderboard(
                event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            )
            self.assertTrue(was_stale)
            now[0] += 1
            with self.assertRaises(server.CacheUnavailable) as caught:
                server.load_leaderboard(
                    event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
                )

        cached = cache._entries[server._leaderboard_cache_key(event_id)].value
        encoded = json.dumps({"cached": cached, "fresh": fresh, "stale": stale})
        self.assertEqual(fresh[0]["id"], 7)
        self.assertTrue(fresh[0]["is_anonymous"])
        self.assertEqual(fresh[0]["name"], "Anonymous Participant")
        self.assertIsNone(fresh[0]["bib"])
        self.assertEqual(str(caught.exception), "cached source unavailable")
        for sentinel in ("PRIVATE_ANONYMOUS_NAME_SENTINEL", "PRIVATE_LEADERBOARD_EMAIL_SENTINEL",
                         "PRIVATE_LEADERBOARD_CUSTOM_SENTINEL", "PRIVATE_LEADERBOARD_UNKNOWN_SENTINEL",
                         "PRIVATE_LEADERBOARD_FAILURE_SENTINEL"):
            self.assertNotIn(sentinel, encoded)

    def test_later_page_anonymity_masks_identity_in_final_leaderboard_rows(self):
        pages = [
            ({"leaderboard": [{
                "id": 7, "name": "PRIVATE_LATE_ANON_NAME_SENTINEL", "bib": "17",
                "last_split_index": 1, "last_split_time": 700,
                "unknown": {"deep": "PRIVATE_LATE_ANON_UNKNOWN_SENTINEL"},
            }], "total": 2, "has_more": True}, False),
            ({"leaderboard": [{"id": 7, "is_anonymous": True}, {"id": 8, "name": "Public"}],
              "total": 2, "has_more": False}, False),
        ]
        with patch.object(server, "upstream_json", side_effect=pages):
            rows, stale = server.load_leaderboard("alpha-run-5k-2026")

        self.assertFalse(stale)
        self.assertEqual(rows[0]["name"], "Anonymous Participant")
        self.assertIsNone(rows[0]["bib"])
        self.assertNotIn("PRIVATE_LATE_ANON", json.dumps(rows))

    def test_dynamic_two_page_leaderboard_cache_contains_only_post_union_event_value(self):
        event_id = "alpha-run-5k-2026"
        now = [0.0]
        refresh = [False]
        pages = {
            0: {
                "status": "ok", "total": 2, "has_more": True,
                "leaderboard": [{
                    "id": 7, "name": "PRIVATE_PAGE_ONE_IDENTITY_SENTINEL", "bib": "17",
                    "last_split_index": 1, "last_split_time": 700,
                    "unknown": {"deep": ["PRIVATE_PAGE_ONE_RECURSIVE_SENTINEL"]},
                }],
            },
            1: {
                "status": "ok", "total": 2, "has_more": False,
                "leaderboard": [
                    {"id": "0007", "athlete_anonymous": True},
                    {"id": 8, "name": "Public Runner", "bib": "18"},
                ],
            },
        }

        def fetch(request, timeout):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            offset = int(query["offset"][0])
            if refresh[0] and offset == 1:
                raise OSError("PRIVATE_PAGE_TWO_REFRESH_FAILURE_SENTINEL")
            return FakeResponse(json.dumps(pages[offset]).encode())

        cache = server.JsonCache(clock=lambda: now[0])
        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", side_effect=fetch) as opened:
            rows, stale = server.load_leaderboard(
                event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            )
            refresh[0] = True
            now[0] = server.LEADERBOARD_TTL_SECONDS
            fallback, fallback_stale = server.load_leaderboard(
                event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            )

        self.assertFalse(stale)
        self.assertTrue(fallback_stale)
        self.assertEqual(fallback, rows)
        self.assertEqual(opened.call_count, 4)
        self.assertEqual(len(cache._entries), 1)
        self.assertEqual(list(cache._entries), [server._leaderboard_cache_key(event_id)])
        self.assertFalse(any("offset=" in key for key in cache._entries))
        self.assertEqual(rows[0]["id"], 7)
        self.assertTrue(rows[0]["is_anonymous"])
        self.assertEqual(rows[0]["name"], "Anonymous Participant")
        self.assertIsNone(rows[0]["bib"])
        self.assertEqual(rows[1]["name"], "Public Runner")
        assert_recursive_absent(
            self, cache._entries,
            "PRIVATE_PAGE_ONE_IDENTITY_SENTINEL", "PRIVATE_PAGE_ONE_RECURSIVE_SENTINEL",
            "PRIVATE_PAGE_TWO_REFRESH_FAILURE_SENTINEL",
        )

    def test_dynamic_gps_cache_has_an_inclusive_stale_boundary(self):
        event_id = "alpha-run-5k-2026"
        path = f"/gps/locations/{event_id}"
        raw = {"status": "ok", "positions": [{
            "runner_id": 7, "runner_name": "Runner", "latitude": 45.0, "longitude": -111.0,
            "recorded_at": "2026-09-14T17:59:50Z",
            "unknown": {"deep": "PRIVATE_GPS_BOUNDARY_SENTINEL"},
        }]}
        now = [0.0]
        cache = server.JsonCache(clock=lambda: now[0])
        failures = [FakeResponse(json.dumps(raw).encode()), OSError("PRIVATE_GPS_FAILURE_SENTINEL"),
                    OSError("PRIVATE_GPS_FAILURE_SENTINEL")]
        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", side_effect=failures):
            fresh, stale = server.upstream_json(
                path, server.GPS_TTL_SECONDS,
                max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            )
            self.assertFalse(stale)
            now[0] = server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
            fallback, stale = server.upstream_json(
                path, server.GPS_TTL_SECONDS,
                max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            )
            self.assertTrue(stale)
            self.assertEqual(fallback, fresh)
            now[0] += 1
            with self.assertRaises(server.CacheUnavailable) as caught:
                server.upstream_json(
                    path, server.GPS_TTL_SECONDS,
                    max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
                )

        encoded = json.dumps({"cache": cache._entries[server.UPSTREAM + path].value,
                              "fresh": fresh, "fallback": fallback})
        self.assertNotIn("PRIVATE_GPS_BOUNDARY_SENTINEL", encoded)
        self.assertEqual(str(caught.exception), "cached source unavailable")


class IdentifierAndSelectionTests(unittest.TestCase):
    def test_identifier_validation_rejects_urls_paths_encoding_queries_and_controls(self):
        bad = (
            "", " ", "../event", "event/child", "event\\child", "event%2fchild",
            "event?x=1", "event#fragment", "https://example.com/event", "//example.com/event",
            "event\nname", ".hidden", "Upper-Case", "a" * (catalog.MAX_IDENTIFIER_LENGTH + 1),
        )
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(catalog.InvalidIdentifier):
                    catalog.validate_identifier(value)
        self.assertEqual(catalog.validate_identifier("safe-event-5k-2026"), "safe-event-5k-2026")

    def test_invalid_identifier_is_rejected_before_catalog_or_dynamic_fetch(self):
        cache = Mock()
        with patch.object(server, "load_event_bundle") as dynamic_fetch:
            with self.assertRaises(catalog.InvalidIdentifier):
                server.build_selected_event("https://evil.invalid/x", catalog_cache=cache)
        cache.get.assert_not_called()
        dynamic_fetch.assert_not_called()

    def test_unknown_valid_event_is_rejected_before_dynamic_fetch(self):
        cache = Mock()
        cache.get.return_value = selected_catalog()
        with patch.object(server, "load_event_bundle") as dynamic_fetch:
            with self.assertRaises(catalog.UnknownEvent):
                server.build_selected_event("unknown-event-2026", catalog_cache=cache)
        dynamic_fetch.assert_not_called()


class DynamicEventTests(unittest.TestCase):
    def bundle(self, event_id="alpha-run-5k-2026", limited=False):
        event = {
            "id": event_id,
            "name": "Alpha Run 5K",
            "display_name": "5K",
            "event_date": "2026-09-14T12:00:00.000Z",
            "start_time": "08:00:00",
            "timezone": "America/Denver",
            "course_status": "closed",
            "course_distance_miles": 3.1,
            "split_names": ["Start", "Half", "Finish"] if not limited else ["Start", "Finish"],
            "live_tracking_enabled": not limited,
            "private_config": "metadata-secret",
        }
        if limited:
            return {
                "event_response": {"event": event},
                "course_response": {}, "gps_response": {}, "leaderboard": [],
                "history": [], "history_status": "unavailable", "history_error": "history: unavailable",
                "history_fetched_at": None, "source_freshness": {}, "errors": [], "upstream_stale": False,
            }
        return {
            "event_response": {"event": event, "multipliers": [
                {"split_index": 0, "progress_pct": 0.0},
                {"split_index": 1, "progress_pct": 0.5},
                {"split_index": 2, "progress_pct": 1.0},
            ]},
            "course_response": {"courseMaps": [{
                "eventId": event_id, "color": "#123456", "private_map_key": "map-secret",
                "trackPoints": [{"lat": 45.0, "lng": -111.0, "ele": 1000}, {"lat": 45.01, "lng": -111.01, "ele": 1010}],
                "splitPoints": [{"lat": 45.0, "lng": -111.0, "name": "Start"}, {"lat": 45.005, "lng": -111.005, "name": "Half"}, {"lat": 45.01, "lng": -111.01, "name": "Finish"}],
            }]},
            "gps_response": {"positions": [{
                "runner_id": 7, "runner_name": "Runner", "latitude": 45.005, "longitude": -111.005,
                "recorded_at": "2026-09-14T17:59:50Z", "battery_pct": 88, "device_id": "device-secret",
            }]},
            "leaderboard": [{
                "id": 7, "name": "Runner", "bib": 17, "last_split_index": 2, "last_split_time": 1500,
                "finish_time_seconds": 1500, "chip_start_seconds": 0, "email": "runner-private@example.com",
                "custom_answers": {"secret": "answer-secret"},
            }],
            "history": [{
                "id": 7, "last_split_index": 2, "last_split_time": 1500, "chip_start_seconds": 0,
                "splits": [{"split_index": 0, "elapsed_seconds": 0}, {"split_index": 1, "elapsed_seconds": 700}, {"split_index": 2, "elapsed_seconds": 1500}],
                "phone": "555-private",
            }],
            "history_status": "fresh", "history_error": None, "history_fetched_at": "2026-09-14T17:59:55Z",
            "source_freshness": {}, "errors": [], "upstream_stale": False,
        }

    def test_dynamic_selection_reuses_event_builder_minimizes_and_derives_capabilities(self):
        cache = Mock(); cache.get.return_value = selected_catalog()
        bundle = self.bundle()
        with patch.object(server, "load_checkpoint_history", return_value={"history": bundle["history"], "history_status": "fresh", "history_error": None, "history_fetched_at": bundle["history_fetched_at"]}), \
             patch.object(server, "load_event_bundle", return_value=bundle) as load:
            payload = server.build_selected_event("alpha-run-5k-2026", include_course=True, now=NOW, catalog_cache=cache)
        load.assert_called_once()
        self.assertEqual(load.call_args.args[0], "alpha-run-5k-2026")
        event = payload["events"][0]
        self.assertEqual(event["id"], "alpha-run-5k-2026")
        self.assertIn("course", event)
        self.assertEqual(event["capabilities"], {
            "results": True, "intermediate_splits": True, "course_map": True,
            "elevation": True, "live_tracking": True, "gps": True, "running_eligible": True,
        })
        encoded = json.dumps(payload)
        for forbidden in ("runner-private@example.com", "555-private", "device-secret", "answer-secret", "metadata-secret", "map-secret"):
            self.assertNotIn(forbidden, encoded)
        for field in ("battery_pct", "age", "gender", "city", "state"):
            self.assertNotIn(f'"{field}"', encoded)

    def test_dynamic_source_caches_are_endpoint_allowlisted_before_assignment(self):
        event_id = "alpha-run-5k-2026"
        payloads = {
            f"/events/{event_id}": {
                "status": "ok",
                "event": {
                    "id": event_id, "name": "Alpha Run 5K", "display_name": "5K",
                    "event_date": "2026-09-14T12:00:00.000Z", "start_time": "08:00:00",
                    "timezone": "America/Denver", "course_status": "closed",
                    "course_distance_miles": 3.1, "split_names": ["Start", "Finish"],
                    "split_distances": {"1": {"value": 3.1},
                                        "PRIVATE_SPLIT_DISTANCE_KEY_SENTINEL": {"value": 99}},
                    "live_tracking_enabled": True,
                    "participant_dump": [{"email": "PRIVATE_EVENT_PARTICIPANT_SENTINEL"}],
                },
                "multipliers": [{"split_index": 0, "progress_pct": 0,
                                 "unknown": {"secret": "PRIVATE_MULTIPLIER_SENTINEL"}},
                                {"split_index": 1, "progress_pct": 1}],
            },
            f"/course-maps/event/{event_id}?include=points": {
                "status": "ok", "courseMaps": [{
                    "eventId": event_id, "color": "#123456",
                    "trackPoints": [{"lat": 45.0, "lng": -111.0, "ele": 1000},
                                    {"lat": 45.01, "lng": -111.01, "ele": 1010}],
                    "splitPoints": [{"lat": 45.0, "lng": -111.0, "name": "Start"},
                                    {"lat": 45.01, "lng": -111.01, "name": "Finish"}],
                    "private_map": {"participants": ["PRIVATE_COURSE_PARTICIPANT_SENTINEL"]},
                }],
            },
            f"/gps/locations/{event_id}": {
                "status": "ok", "positions": [{
                    "runner_id": 7, "runner_name": "Runner", "latitude": 45.005,
                    "longitude": -111.005, "recorded_at": "2026-09-14T17:59:50Z",
                    "battery_pct": 88, "device_id": "PRIVATE_GPS_DEVICE_SENTINEL",
                    "custom_answers": {"secret": "PRIVATE_GPS_CUSTOM_SENTINEL"},
                    "unknown": {"nested": ["PRIVATE_GPS_UNKNOWN_SENTINEL"]},
                }],
            },
            f"/events/{event_id}/leaderboard?limit=500&offset=0": {
                "status": "ok", "total": 1, "has_more": False,
                "leaderboard": [{"id": 7, "name": "Runner", "bib": 17,
                                 "email": "PRIVATE_BUNDLE_LEADERBOARD_SENTINEL"}],
            },
        }

        def fetch(request, timeout):
            path = request.full_url.removeprefix(server.UPSTREAM)
            return FakeResponse(json.dumps(payloads[path]).encode())

        cache = server.JsonCache(clock=lambda: 0.0)
        history = {"history": [], "history_status": "unavailable",
                   "history_error": "history: unavailable", "history_fetched_at": None}
        with patch.object(server, "CACHE", cache), patch.object(
                server.urllib.request, "urlopen", side_effect=fetch):
            bundle = server.load_event_bundle(event_id, history, dynamic=True)

        cached = {key: entry.value for key, entry in cache._entries.items()}
        encoded = json.dumps({"cached": cached, "bundle": bundle})
        for sentinel in ("PRIVATE_EVENT_PARTICIPANT_SENTINEL", "PRIVATE_MULTIPLIER_SENTINEL",
                         "PRIVATE_SPLIT_DISTANCE_KEY_SENTINEL",
                         "PRIVATE_COURSE_PARTICIPANT_SENTINEL", "PRIVATE_GPS_DEVICE_SENTINEL",
                         "PRIVATE_GPS_CUSTOM_SENTINEL", "PRIVATE_GPS_UNKNOWN_SENTINEL",
                         "PRIVATE_BUNDLE_LEADERBOARD_SENTINEL"):
            self.assertNotIn(sentinel, encoded)
        self.assertEqual(bundle["event_response"]["event"]["timezone"], "America/Denver")
        self.assertEqual(bundle["course_response"]["courseMaps"][0]["trackPoints"][1],
                         {"lat": 45.01, "lng": -111.01, "ele": 1010})
        self.assertEqual(bundle["gps_response"]["positions"][0]["runner_id"], 7)

    def test_dynamic_bundle_wires_finite_source_specific_stale_limits(self):
        event_id = "alpha-run-5k-2026"

        def source(path, ttl, **policy):
            if path == f"/events/{event_id}":
                value = {"event": {"id": event_id}}
            elif path.startswith("/course-maps/"):
                value = {}
            else:
                value = {"positions": []}
            return value, False

        with patch.object(server, "upstream_json", side_effect=source) as upstream, patch.object(
                server, "load_leaderboard", return_value=([], False)) as leaderboard:
            server.load_event_bundle(event_id, {
                "history": [], "history_status": "unavailable",
                "history_error": "history: unavailable", "history_fetched_at": None,
            }, dynamic=True)

        policies = {call.args[0]: call.kwargs["max_stale_seconds"] for call in upstream.call_args_list}
        self.assertEqual(policies[f"/events/{event_id}"], server.DYNAMIC_EVENT_MAX_STALE_SECONDS)
        self.assertEqual(policies[f"/course-maps/event/{event_id}?include=points"],
                         server.DYNAMIC_COURSE_MAX_STALE_SECONDS)
        self.assertEqual(policies[f"/gps/locations/{event_id}"],
                         server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS)
        leaderboard.assert_called_once_with(
            event_id, max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
        )

    def test_dynamic_selection_wires_bounded_result_and_bundle_loaders(self):
        cache = Mock(); cache.get.return_value = selected_catalog()
        history_value = {"history": [], "history_status": "unavailable",
                         "history_error": "history: unavailable", "history_fetched_at": None}
        bundle = self.bundle(limited=True)
        with patch.object(server, "load_checkpoint_history", return_value=history_value) as history, \
             patch.object(server, "load_event_bundle", return_value=bundle) as load:
            server.build_selected_event("alpha-run-5k-2026", now=NOW, catalog_cache=cache)

        history.assert_called_once_with(
            "alpha-run-5k-2026",
            max_stale_seconds=server.DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
        )
        load.assert_called_once_with("alpha-run-5k-2026", history_value, dynamic=True)

    def test_data_limited_event_is_preserved_with_false_capabilities(self):
        cache = Mock(); cache.get.return_value = selected_catalog()
        bundle = self.bundle(limited=True)
        with patch.object(server, "load_checkpoint_history", return_value={"history": [], "history_status": "unavailable", "history_error": "history: unavailable", "history_fetched_at": None}), \
             patch.object(server, "load_event_bundle", return_value=bundle):
            payload = server.build_selected_event("alpha-run-5k-2026", now=NOW, catalog_cache=cache)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["capabilities"], {
            "results": False, "intermediate_splits": False, "course_map": False,
            "elevation": False, "live_tracking": False, "gps": False, "running_eligible": True,
        })

    def test_capabilities_distinguish_results_live_support_and_current_gps(self):
        bundle = self.bundle(limited=True)
        bundle["event_response"]["event"]["live_tracking_enabled"] = True
        bundle["leaderboard"] = [{"id": 7, "name": "Registered", "status": "REGISTERED"}]

        view = server.build_event_view(bundle, NOW, include_course=False)

        self.assertFalse(view["capabilities"]["results"])
        self.assertTrue(view["capabilities"]["live_tracking"])
        self.assertFalse(view["capabilities"]["gps"])

        bundle["history"] = [{"id": 7, "status": "FINISHED"}]
        bundle["history_status"] = "fresh"
        bundle["history_error"] = None
        bundle["history_fetched_at"] = "2026-09-14T17:59:55Z"
        view = server.build_event_view(bundle, NOW, include_course=False)
        self.assertTrue(view["capabilities"]["results"])

    def test_missing_event_timezone_never_invents_a_montana_start_clock(self):
        bundle = self.bundle(limited=True)
        event = bundle["event_response"]["event"]
        event.pop("timezone")

        view = server.build_event_view(bundle, NOW, include_course=False)

        self.assertIsNone(server.event_start_utc(event))
        self.assertIsNone(view["start_at"])
        self.assertIsNone(view["timezone"])

    def test_mismatched_upstream_event_is_a_bounded_failure(self):
        cache = Mock(); cache.get.return_value = selected_catalog()
        bundle = self.bundle(event_id="different-event-2026", limited=True)
        with patch.object(server, "load_checkpoint_history", return_value={}), \
             patch.object(server, "load_event_bundle", return_value=bundle):
            with self.assertRaises(server.SelectedEventUnavailable):
                server.build_selected_event("alpha-run-5k-2026", now=NOW, catalog_cache=cache)

    def test_cold_dynamic_event_failure_cannot_validate_request_derived_identity(self):
        event_id = "alpha-run-5k-2026"
        cache = Mock(); cache.get.return_value = selected_catalog()
        with patch.object(server, "CACHE", server.JsonCache()), patch.object(
                server.urllib.request, "urlopen",
                side_effect=OSError("PRIVATE_COLD_EVENT_FAILURE_SENTINEL")):
            with self.assertRaises(server.SelectedEventUnavailable) as caught:
                server.build_selected_event(event_id, now=NOW, catalog_cache=cache)
        self.assertEqual(str(caught.exception), "selected event unavailable")
        self.assertNotIn("PRIVATE_COLD_EVENT_FAILURE_SENTINEL", str(caught.exception))

        history = {"history": [], "history_status": "unavailable",
                   "history_error": "history: unavailable", "history_fetched_at": None}
        with patch.object(server, "upstream_json", side_effect=OSError("offline")), \
             patch.object(server, "load_leaderboard", side_effect=OSError("offline")):
            dynamic = server.load_event_bundle(event_id, history, dynamic=True)
            legacy = server.load_event_bundle(event_id, history, dynamic=False)
        self.assertEqual(dynamic["event_response"], {})
        self.assertEqual(legacy["event_response"], {"event": {"id": event_id}})


class EndpointTests(unittest.TestCase):
    def test_local_catalog_and_event_get_routes_validate_and_bound_errors(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.RutHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            with patch.object(server, "build_catalog_payload", return_value={"query": "alpha", "races": []}) as search:
                with urllib.request.urlopen(base + "/api/catalog?q=alpha&year=2026&limit=5") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)["races"], [])
                search.assert_called_once_with("alpha", year="2026", limit="5")
            with patch.object(server, "build_selected_event", return_value={"events": [{"id": "alpha-run-5k-2026"}]}) as selected:
                with urllib.request.urlopen(base + "/api/event?event_id=alpha-run-5k-2026&include_course=1") as response:
                    self.assertEqual(response.status, 200)
                selected.assert_called_once_with("alpha-run-5k-2026", include_course=True)
            with patch.object(server, "build_selected_event", side_effect=RuntimeError("private upstream body")):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + "/api/event?event_id=alpha-run-5k-2026")
                self.assertEqual(caught.exception.code, 503)
                self.assertNotIn("private upstream body", caught.exception.read().decode())
            for path in ("/api/catalog", "/api/catalog?q=%20", "/api/catalog?q=x",
                         "/api/event?event_id=..%2Fevil",
                         "/api/event?event_id=alpha-run-5k-2026&include_course=yes"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + path)
                self.assertEqual(caught.exception.code, 400)
            with urllib.request.urlopen(base + "/course_signal_shell.js") as response:
                self.assertEqual(response.status, 200)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()

    def test_local_event_route_returns_503_on_cold_event_source_failure(self):
        event_id = "alpha-run-5k-2026"
        catalog_cache = Mock(); catalog_cache.get.return_value = selected_catalog()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.RutHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        url = f"http://127.0.0.1:{httpd.server_port}/api/event?event_id={event_id}"
        real_urlopen = urllib.request.urlopen

        def fail_only_upstream(target, *args, **kwargs):
            target_url = target.full_url if hasattr(target, "full_url") else str(target)
            if target_url.startswith(server.UPSTREAM):
                raise OSError("PRIVATE_COLD_ENDPOINT_SENTINEL")
            return real_urlopen(target, *args, **kwargs)

        try:
            with patch.object(server, "CATALOG", catalog_cache), \
                 patch.object(server, "CACHE", server.JsonCache()), \
                 patch.object(server.urllib.request, "urlopen", side_effect=fail_only_upstream):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(url)
                self.assertEqual(caught.exception.code, 503)
                body = caught.exception.read().decode()
            self.assertEqual(json.loads(body), {
                "status": "error", "message": "Event data unavailable; retry shortly",
            })
            self.assertNotIn("PRIVATE_COLD_ENDPOINT_SENTINEL", body)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()

    def test_vercel_catalog_event_routing_and_legacy_compatibility(self):
        with patch.object(server, "build_catalog_payload", return_value={"status": "ok", "races": []}):
            self.assertEqual(response_for("catalog", {"q": ["alpha"]}), (200, {"status": "ok", "races": []}))
        with patch.object(server, "build_selected_event", return_value={"summary": {"errors": 0}, "events": [{"runners": []}]}):
            status, payload = response_for("event", {"event_id": ["alpha-run-5k-2026"], "include_course": ["1"]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["delivery"], "live_api")
        with patch("api.index.server.build_payload", return_value={"summary": {"errors": 0}, "events": [{"course": {"track_points": [{}, {}]}}]}):
            self.assertEqual(response_for("bootstrap")[0], 200)
            self.assertEqual(response_for("live")[0], 200)

    def test_vercel_bundle_and_routes_expose_dynamic_catalog_without_catchall_loss(self):
        config = json.loads(Path("vercel.json").read_text())
        function = config["functions"]["api/index.py"]
        self.assertIn("competitive_catalog.py", function["includeFiles"])
        api_route = next(route for route in config["routes"] if route["dest"].startswith("/api?route=$1"))
        pattern = re.compile(api_route["src"])
        for route in ("health", "bootstrap", "live", "catalog", "event"):
            with self.subTest(route=route):
                self.assertIsNotNone(pattern.fullmatch(f"/api/{route}"))


if __name__ == "__main__":
    unittest.main()
