"""Fixed-event Results API adapter and privacy contracts."""
from __future__ import annotations

import contextlib
import copy
import http.client
import io
import json
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server
from api import index as vercel_api


EVENT_ID = "the-rut-28k-2026"
RACE_SLUG = "the-rut"
MAX_RUNNER_ID = 9_007_199_254_740_991
PRIVATE_SENTINEL = "PRIVATE_RESULTS_SENTINEL"


def result_row(runner_id=42, name="Ada Runner", bib="42"):
    return {
        "id": runner_id,
        "event_id": EVENT_ID,
        "name": name,
        "bib": bib,
        "age": 36,
        "gender": "X",
        "age_group": "X 30-39",
        "chip_age_group_place": 2,
        "chip_gender_place": 3,
        "status": "FINISHED",
        "chip_start_seconds": 0.0,
        "last_split_index": 2,
        "last_split_time": 200.0,
        "finish_time_seconds": 200.0,
        "finish_place": 1,
        "splits": [
            {"split_index": 0, "elapsed_seconds": 0.0, "cumulative_place": 2},
            {"split_index": 1, "elapsed_seconds": 100.0, "cumulative_place": 1},
            {"split_index": 2, "elapsed_seconds": 200.0, "cumulative_place": 1},
        ],
        "email": PRIVATE_SENTINEL,
        "metadata": {"private": PRIVATE_SENTINEL},
    }


def completed_value(*, include_runner=True):
    rows = [result_row()] if include_runner else []
    rows.append(result_row(43, "Peer Runner", "43"))
    return {"results": rows, "leaderboard": [{"id": 42, "is_anonymous": False}]}


def event_response(*, event_id=EVENT_ID):
    return {
        "status": "ok",
        "event": {
            "id": event_id,
            "name": "The Rut 28K",
            "display_name": "28K",
            "event_date": "2026-09-13T07:00:00-06:00",
            "split_names": ["Start", "Aid", "Finish"],
            "private": PRIVATE_SENTINEL,
        },
        "multipliers": [
            {"split_index": 0, "progress_pct": 0.0},
            {"split_index": 1, "progress_pct": 0.5},
            {"split_index": 2, "progress_pct": 1.0},
        ],
        "private": PRIVATE_SENTINEL,
    }


def course_map(*, aliases=None):
    identity = {"eventId": EVENT_ID} if aliases is None else dict(aliases)
    return {
        **identity,
        "id": "provider-course-id",
        "color": "#ffffff",
        "trackPoints": [
            {"lat": 45.0, "lng": -111.0, "ele": 1000.0, "private": PRIVATE_SENTINEL},
            {"lat": 45.002, "lng": -111.002, "ele": 1100.0},
            {"lat": 45.004, "lng": -111.004, "ele": 1000.0},
        ],
        "splitPoints": [
            {"lat": 45.0, "lng": -111.0},
            {"lat": 45.002, "lng": -111.002},
            {"lat": 45.004, "lng": -111.004},
        ],
        "private": PRIVATE_SENTINEL,
    }


class FakeCompletedCache:
    def __init__(self, value=None, *, stale=False, error=None):
        self.value = completed_value() if value is None else value
        self.stale = stale
        self.error = error
        self.calls = []

    def get(self, event_id):
        self.calls.append(event_id)
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.value)


class FakeUpstream:
    def __init__(self, *, event=None, course=None, event_error=None, course_error=None,
                 event_stale=False, course_stale=False):
        self.event = event_response() if event is None else event
        self.course = {"courseMaps": [course_map()]} if course is None else course
        self.event_error = event_error
        self.course_error = course_error
        self.event_stale = event_stale
        self.course_stale = course_stale
        self.calls = []

    def __call__(self, path, ttl, **kwargs):
        self.calls.append((path, ttl, kwargs))
        if path == f"/events/{EVENT_ID}":
            if self.event_error is not None:
                raise self.event_error
            return copy.deepcopy(self.event), self.event_stale
        if path == f"/course-maps/event/{EVENT_ID}?include=points":
            if self.course_error is not None:
                raise self.course_error
            return copy.deepcopy(self.course), self.course_stale
        raise AssertionError(f"unexpected upstream path: {path}")


def query(**values):
    return {key: [value] for key, value in values.items()}


@contextlib.contextmanager
def running(handler_class, *, cache, upstream):
    with patch.object(server, "RESULTS_CACHE", cache), patch.object(server, "upstream_json", upstream):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)


def http_get(base, target, headers=None):
    parsed = urllib.parse.urlsplit(base)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request("GET", target, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        connection.close()


class ParameterBuilderTests(unittest.TestCase):
    def test_search_parameters_are_exact_normalized_and_bounded_by_code_points(self):
        self.assertEqual(
            server.results_search_request_parameters(query(event_id=EVENT_ID, q="  José\t  🏃  ")),
            (EVENT_ID, "José 🏃", 20),
        )
        self.assertEqual(
            server.results_search_request_parameters(query(event_id=EVENT_ID, q="ab", limit="1")),
            (EVENT_ID, "ab", 1),
        )
        self.assertEqual(
            server.results_search_request_parameters(query(event_id=EVENT_ID, q="x" * 100, limit="20")),
            (EVENT_ID, "x" * 100, 20),
        )

    def test_search_rejects_missing_unknown_duplicate_blank_and_noncanonical_values(self):
        invalid = [
            {},
            query(event_id=EVENT_ID),
            query(q="Ada"),
            query(event_id="", q="Ada"),
            query(event_id=EVENT_ID, q=""),
            query(event_id=EVENT_ID, q=" "),
            query(event_id=EVENT_ID, q="x"),
            query(event_id=EVENT_ID, q="x" * 101),
            query(event_id=EVENT_ID, q="a\x00b"),
            query(event_id=EVENT_ID, q="Ada", extra="1"),
            {"event_id": [EVENT_ID, EVENT_ID], "q": ["Ada"]},
            {"event_id": [EVENT_ID], "q": ["Ada", "Runner"]},
            {"event_id": EVENT_ID, "q": ["Ada"]},
        ]
        invalid.extend(query(event_id=EVENT_ID, q="Ada", limit=value)
                       for value in ("", "0", "00", "01", "+1", "1 ", "21", "１２", "2.0"))
        for parameters in invalid:
            with self.subTest(parameters=parameters), self.assertRaises(server.ResultsRequestError):
                server.results_search_request_parameters(parameters)

    def test_detail_accepts_only_canonical_positive_safe_ascii_runner_ids(self):
        self.assertEqual(
            server.results_runner_request_parameters(
                query(event_id=EVENT_ID, runner_id=str(MAX_RUNNER_ID))
            ),
            (EVENT_ID, MAX_RUNNER_ID),
        )
        for value in ("", "0", "00", "01", "+1", "1 ", str(MAX_RUNNER_ID + 1), "１２", "1.0"):
            with self.subTest(value=value), self.assertRaises(server.ResultsRequestError):
                server.results_runner_request_parameters(query(event_id=EVENT_ID, runner_id=value))
        for parameters in (
            {}, query(event_id=EVENT_ID), query(runner_id="42"),
            query(event_id=EVENT_ID, runner_id="42", extra="1"),
            {"event_id": [EVENT_ID, EVENT_ID], "runner_id": ["42"]},
            {"event_id": [EVENT_ID], "runner_id": ["42", "43"]},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(server.ResultsRequestError):
                server.results_runner_request_parameters(parameters)

    def test_well_formed_foreign_event_is_not_found_before_any_io(self):
        cache = FakeCompletedCache()
        upstream = FakeUpstream()
        for route, parameters in (
            ("results/search", query(event_id="other-race-2026", q="Ada")),
            ("results/runner", query(event_id="other-race-2026", runner_id="42")),
        ):
            with self.subTest(route=route):
                status, payload = server.results_response(
                    route, parameters, completed_cache=cache, upstream_loader=upstream,
                )
                self.assertEqual(status, 404)
                self.assertEqual(payload, {"status": "error", "message": "Results not found"})
        self.assertEqual(cache.calls, [])
        self.assertEqual(upstream.calls, [])

    def test_search_accepts_every_rut_2026_distance_and_uses_its_event_local_cache(self):
        for event_id, _label in server.EVENTS:
            cache = FakeCompletedCache()
            status, payload = server.results_response(
                "results/search", query(event_id=event_id, q="Ada"),
                completed_cache=cache,
                upstream_loader=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("I/O")),
            )
            with self.subTest(event_id=event_id):
                self.assertEqual(status, 200)
                self.assertEqual(payload["event_id"], event_id)
                self.assertEqual(cache.calls, [event_id])

    def test_runner_detail_binds_the_selected_nondefault_rut_event(self):
        event_id = "the-rut-21k-2026"
        value = completed_value()
        for row in value["results"]:
            row["event_id"] = event_id
        cache = FakeCompletedCache(value)
        event = event_response(event_id=event_id)
        course = {"courseMaps": [course_map(aliases={"eventId": event_id})]}
        calls = []

        def upstream(path, _ttl, **_kwargs):
            calls.append(path)
            if path == f"/events/{event_id}":
                return copy.deepcopy(event), False
            if path == f"/course-maps/event/{event_id}?include=points":
                return copy.deepcopy(course), False
            raise AssertionError(f"unexpected upstream path: {path}")

        status, payload = server.results_response(
            "results/runner", query(event_id=event_id, runner_id="42"),
            completed_cache=cache, upstream_loader=upstream,
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["event"]["id"], event_id)
        self.assertEqual(cache.calls, [event_id])
        self.assertEqual(calls, [
            f"/events/{event_id}",
            f"/course-maps/event/{event_id}?include=points",
        ])

    def test_malformed_errors_are_generic_and_do_not_echo_values(self):
        sentinel = "PRIVATE_QUERY_VALUE"
        status, payload = server.results_response(
            "results/search", query(event_id=EVENT_ID, q=sentinel, unknown=sentinel),
            completed_cache=FakeCompletedCache(), upstream_loader=FakeUpstream(),
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"status": "error", "message": "Invalid results request"})
        self.assertNotIn(sentinel, json.dumps(payload))
        self.assertNotIn(EVENT_ID, json.dumps(payload))


class BuilderIsolationTests(unittest.TestCase):
    def test_search_uses_only_completed_cache_and_pure_search(self):
        cache = FakeCompletedCache(stale=True)

        def no_upstream(*_args, **_kwargs):
            raise AssertionError("search attempted upstream I/O")

        with patch.object(server.CATALOG, "get", side_effect=AssertionError("catalog used")), \
                patch.object(server, "build_payload", side_effect=AssertionError("live bundle used")), \
                patch.object(server, "build_selected_event", side_effect=AssertionError("selected event used")):
            status, payload = server.results_response(
                "results/search", query(event_id=EVENT_ID, q="Ada"),
                completed_cache=cache, upstream_loader=no_upstream,
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["event_id"], EVENT_ID)
        self.assertIs(payload["stale"], True)
        self.assertEqual([row["id"] for row in payload["matches"]], [42])
        self.assertEqual(cache.calls, [EVENT_ID])

    def test_valid_no_match_search_is_200_even_when_stale(self):
        status, payload = server.results_response(
            "results/search", query(event_id=EVENT_ID, q="Nobody"),
            completed_cache=FakeCompletedCache(stale=True),
            upstream_loader=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("I/O")),
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok", "event_id": EVENT_ID, "stale": True, "matches": []})

    def test_detail_uses_only_completed_event_and_exact_course_sources(self):
        cache = FakeCompletedCache()
        upstream = FakeUpstream()
        with patch.object(server.CATALOG, "get", side_effect=AssertionError("catalog used")), \
                patch.object(server, "build_payload", side_effect=AssertionError("live bundle used")), \
                patch.object(server, "load_event_bundle", side_effect=AssertionError("live event bundle used")), \
                patch.object(server, "load_checkpoint_history", side_effect=AssertionError("history used")), \
                patch.object(server, "load_leaderboard", side_effect=AssertionError("live leaderboard used")):
            status, payload = server.results_response(
                "results/runner", query(event_id=EVENT_ID, runner_id="42"),
                completed_cache=cache, upstream_loader=upstream,
            )
        self.assertEqual(status, 200)
        self.assertEqual(cache.calls, [EVENT_ID])
        self.assertEqual(
            [call[0] for call in upstream.calls],
            [f"/events/{EVENT_ID}", f"/course-maps/event/{EVENT_ID}?include=points"],
        )
        self.assertEqual(payload["race"], {"slug": RACE_SLUG, "name": "The Rut"})
        self.assertEqual(payload["event"]["id"], EVENT_ID)
        self.assertEqual(payload["analysis"]["runner"]["id"], 42)

    def test_required_event_unavailable_or_wrong_is_503_with_generic_text(self):
        cases = (
            FakeUpstream(event_error=RuntimeError(f"provider {PRIVATE_SENTINEL}")),
            FakeUpstream(event=event_response(event_id="other-race-2026")),
            FakeUpstream(event={"event": {"id": EVENT_ID}}),
        )
        for upstream in cases:
            with self.subTest(upstream=upstream):
                status, payload = server.results_response(
                    "results/runner", query(event_id=EVENT_ID, runner_id="42"),
                    completed_cache=FakeCompletedCache(), upstream_loader=upstream,
                )
                self.assertEqual(status, 503)
                self.assertEqual(payload, {"status": "error", "message": "Results unavailable; retry shortly"})
                self.assertNotIn(PRIVATE_SENTINEL, json.dumps(payload))
                self.assertNotIn("provider", json.dumps(payload).lower())

    def test_completed_unavailable_is_503(self):
        status, payload = server.results_response(
            "results/search", query(event_id=EVENT_ID, q="Ada"),
            completed_cache=FakeCompletedCache(error=RuntimeError(PRIVATE_SENTINEL)),
            upstream_loader=FakeUpstream(),
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["message"], "Results unavailable; retry shortly")
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(payload))

    def test_fresh_runner_miss_is_404_but_stale_miss_is_503(self):
        for stale, expected in ((False, 404), (True, 503)):
            cache = FakeCompletedCache(completed_value(include_runner=False), stale=stale)
            status, payload = server.results_response(
                "results/runner", query(event_id=EVENT_ID, runner_id="42"),
                completed_cache=cache, upstream_loader=FakeUpstream(),
            )
            self.assertEqual(status, expected)
            self.assertNotIn("42", json.dumps(payload))
            self.assertNotIn(EVENT_ID, json.dumps(payload))

    def test_stale_event_or_used_course_marks_detail_stale(self):
        for upstream in (FakeUpstream(event_stale=True), FakeUpstream(course_stale=True)):
            with self.subTest(calls=upstream.calls):
                status, payload = server.results_response(
                    "results/runner", query(event_id=EVENT_ID, runner_id="42"),
                    completed_cache=FakeCompletedCache(), upstream_loader=upstream,
                )
                self.assertEqual(status, 200)
                self.assertIs(payload["stale"], True)


class CourseBindingAndPrivacyTests(unittest.TestCase):
    def test_upstream_result_projection_keeps_only_public_demographics_and_scrubs_anonymous_duplicates(self):
        public = result_row()
        public.update({"gun_age_group_place": 4, "gun_gender_place": 5})
        public_projected = server.sanitize_result_payload({"status": "ok", "results": [public]})
        self.assertEqual(
            {key: public_projected["results"][0][key] for key in (
                "age", "gender", "age_group", "chip_age_group_place", "gun_age_group_place",
                "chip_gender_place", "gun_gender_place",
            )},
            {
                "age": 36, "gender": "X", "age_group": "X 30-39",
                "chip_age_group_place": 2, "gun_age_group_place": 4,
                "chip_gender_place": 3, "gun_gender_place": 5,
            },
        )
        self.assertNotIn("email", public_projected["results"][0])
        duplicate = {
            "id": 42,
            "event_id": EVENT_ID,
            "athlete_anonymous": True,
            "age": 99,
            "gender": "PRIVATE GENDER",
            "age_group": "PRIVATE GROUP",
        }
        projected = server.sanitize_result_payload({"status": "ok", "results": [public, duplicate]})

        self.assertEqual(projected["results"][0]["name"], "Anonymous Participant")
        self.assertNotIn("name", projected["results"][1])
        for row in projected["results"]:
            if "bib" in row:
                self.assertIsNone(row["bib"])
            for field in (
                    "age", "gender", "age_group", "chip_age_group_place",
                    "gun_age_group_place", "chip_gender_place", "gun_gender_place"):
                self.assertNotIn(field, row)
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(projected))

    def test_exact_event_bound_course_enables_bounded_public_geometry(self):
        exact = course_map(aliases={"event_id": EVENT_ID, "eventId": EVENT_ID})
        status, payload = server.results_response(
            "results/runner", query(event_id=EVENT_ID, runner_id="42"),
            completed_cache=FakeCompletedCache(),
            upstream_loader=FakeUpstream(course={"courseMaps": [exact]}),
        )
        self.assertEqual(status, 200)
        capabilities = payload["event"]["capabilities"]
        self.assertEqual(capabilities, {
            "results": True,
            "intermediate_splits": True,
            "course_map": True,
            "elevation": True,
            "live_gps": False,
        })
        self.assertTrue(all(type(value) is bool for value in capabilities.values()))
        self.assertEqual(set(payload["event"]["course"]), {"track_points", "progress_points"})
        self.assertEqual(payload["event"]["course"]["progress_points"], [0.0, 0.5, 1.0])
        self.assertEqual(len(payload["event"]["course"]["track_points"]), 3)
        self.assertIs(payload["analysis"]["capabilities"]["terrain"], True)

    def test_foreign_untagged_conflicting_and_ambiguous_courses_are_unavailable(self):
        invalid_responses = (
            {"courseMaps": [course_map(aliases={"eventId": "other-race-2026"})]},
            {"courseMaps": [course_map(aliases={})]},
            {"courseMaps": [course_map(aliases={"eventId": EVENT_ID, "event_id": "other-race-2026"})]},
            {"courseMaps": [course_map(), course_map()]},
        )
        for course in invalid_responses:
            with self.subTest(course=course):
                status, payload = server.results_response(
                    "results/runner", query(event_id=EVENT_ID, runner_id="42"),
                    completed_cache=FakeCompletedCache(), upstream_loader=FakeUpstream(course=course),
                )
                self.assertEqual(status, 200)
                self.assertNotIn("course", payload["event"])
                self.assertIs(payload["event"]["capabilities"]["course_map"], False)
                self.assertIs(payload["event"]["capabilities"]["elevation"], False)
                self.assertIs(payload["analysis"]["capabilities"]["terrain"], False)
                self.assertIs(payload["analysis"]["capabilities"]["active_energy"], False)

    def test_course_failure_or_unusable_progress_degrades_to_split_analysis(self):
        cases = (
            FakeUpstream(course_error=RuntimeError(PRIVATE_SENTINEL)),
            FakeUpstream(event={
                **event_response(),
                "multipliers": [{"split_index": 1, "progress_pct": 0.0}],
            }),
        )
        for upstream in cases:
            with self.subTest(upstream=upstream):
                status, payload = server.results_response(
                    "results/runner", query(event_id=EVENT_ID, runner_id="42"),
                    completed_cache=FakeCompletedCache(), upstream_loader=upstream,
                )
                self.assertEqual(status, 200)
                self.assertIs(payload["analysis"]["capabilities"]["splits"], True)
                self.assertIs(payload["analysis"]["capabilities"]["terrain"], False)

    def test_detail_and_search_are_strict_public_allowlists(self):
        upstream = FakeUpstream()
        detail_status, detail = server.results_response(
            "results/runner", query(event_id=EVENT_ID, runner_id="42"),
            completed_cache=FakeCompletedCache(), upstream_loader=upstream,
        )
        search_status, search = server.results_response(
            "results/search", query(event_id=EVENT_ID, q="Ada"),
            completed_cache=FakeCompletedCache(), upstream_loader=upstream,
        )
        self.assertEqual((detail_status, search_status), (200, 200))
        self.assertEqual(set(detail), {"status", "stale", "race", "event", "analysis"})
        self.assertEqual(set(detail["race"]), {"slug", "name"})
        self.assertEqual(set(detail["event"]), {"id", "label", "event_date", "capabilities", "course"})
        self.assertEqual(set(search), {"status", "event_id", "stale", "matches"})
        self.assertEqual(
            set(search["matches"][0]),
            {"id", "name", "bib", "status", "finish_seconds", "finish_place",
             "age", "gender", "age_group", "age_group_place", "gender_place"},
        )
        rendered = json.dumps({"detail": detail, "search": search})
        self.assertNotIn(PRIVATE_SENTINEL, rendered)
        self.assertNotIn("Peer Runner", rendered)
        for key in ("email", "metadata", "leaderboard", "positions", "gps_response"):
            self.assertNotIn(f'"{key}"', rendered)


class AdapterTests(unittest.TestCase):
    def test_managed_response_and_shared_response_are_identical_with_injected_sources(self):
        parameters = query(event_id=EVENT_ID, runner_id="42")
        expected = server.results_response(
            "results/runner", parameters,
            completed_cache=FakeCompletedCache(), upstream_loader=FakeUpstream(),
        )
        actual = vercel_api.response_for(
            "results/runner", parameters,
            completed_cache=FakeCompletedCache(), upstream_loader=FakeUpstream(),
        )
        self.assertEqual(actual, expected)

    def test_vercel_rewrite_headers_preserve_results_parameters_without_query_logging(self):
        headers = {
            "X-Course-Signal-Event": EVENT_ID,
            "X-Course-Signal-Runner": "42",
        }
        with running(vercel_api.handler, cache=FakeCompletedCache(), upstream=FakeUpstream()) as base:
            status, response_headers, payload = http_get(
                base, "/api?route=results%2Frunner", headers=headers,
            )
            options_status, options_headers, _ = http_get(base, "/api/results/runner")
        self.assertEqual(status, 200)
        self.assertEqual(payload["analysis"]["runner"]["id"], 42)
        self.assertEqual(response_headers["Cache-Control"], "no-store")
        self.assertIn("X-Course-Signal-Runner", response_headers["Access-Control-Allow-Headers"])
        self.assertEqual(options_status, 400)
        self.assertIn("X-Course-Signal-Event", options_headers["Access-Control-Allow-Headers"])

    def test_vercel_results_header_transport_rejects_missing_extra_and_duplicate_values(self):
        cases = (
            {"X-Course-Signal-Event": EVENT_ID},
            {"X-Course-Signal-Event": EVENT_ID, "X-Course-Signal-Runner": "42", "X-Course-Signal-Query": "Ada"},
        )
        cache = FakeCompletedCache()
        upstream = FakeUpstream()
        with running(vercel_api.handler, cache=cache, upstream=upstream) as base:
            for headers in cases:
                status, _, payload = http_get(base, "/api?route=results%2Frunner", headers=headers)
                self.assertEqual(status, 400)
                self.assertEqual(payload["message"], "Invalid results request")
        self.assertEqual(cache.calls, [])
        self.assertEqual(upstream.calls, [])

    def test_direct_nested_routes_have_no_store_headers_in_both_adapters(self):
        target = f"/api/results/runner?event_id={EVENT_ID}&runner_id=42"
        local_cache = FakeCompletedCache()
        local_upstream = FakeUpstream()
        with running(server.RutHandler, cache=local_cache, upstream=local_upstream) as base:
            local_status, local_headers, local_payload = http_get(base, target)
        managed_cache = FakeCompletedCache()
        managed_upstream = FakeUpstream()
        with running(vercel_api.handler, cache=managed_cache, upstream=managed_upstream) as base:
            managed_status, managed_headers, managed_payload = http_get(base, target)

        self.assertEqual((local_status, managed_status), (200, 200))
        self.assertEqual(local_payload, managed_payload)
        self.assertEqual(local_headers["Cache-Control"], "no-store")
        self.assertEqual(managed_headers["Cache-Control"], "no-store")
        self.assertEqual(managed_headers["Vercel-CDN-Cache-Control"], "no-store")
        self.assertEqual(local_cache.calls, [EVENT_ID])
        self.assertEqual(managed_cache.calls, [EVENT_ID])

    def test_direct_handler_rejects_duplicate_and_unknown_results_parameters(self):
        cache = FakeCompletedCache()
        upstream = FakeUpstream()
        targets = (
            f"/api/results/search?event_id={EVENT_ID}&q=Ada&q=Runner",
            f"/api/results/search?event_id={EVENT_ID}&q=Ada&unknown=1",
            f"/api/results/runner?event_id={EVENT_ID}&runner_id=42&runner_id=43",
            f"/api/results/runner?event_id={EVENT_ID}&runner_id=42&bad=%ZZ",
        )
        for handler_class in (server.RutHandler, vercel_api.handler):
            with self.subTest(handler=handler_class.__module__), \
                    running(handler_class, cache=cache, upstream=upstream) as base:
                for target in targets:
                    status, headers, payload = http_get(base, target)
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["message"], "Invalid results request")
                    self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(cache.calls, [])
        self.assertEqual(upstream.calls, [])

    def test_local_results_logs_omit_raw_and_encoded_query_values_only(self):
        raw = "RAW_RESULTS_LOG_SENTINEL"
        encoded_plain = "ENCODED_RESULTS_LOG_SENTINEL"
        encoded = urllib.parse.quote(encoded_plain)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                running(server.RutHandler, cache=FakeCompletedCache(), upstream=FakeUpstream()) as base:
            http_get(base, f"/api/results/search?event_id={EVENT_ID}&q={raw}")
            http_get(base, f"/api/results/search?event_id={EVENT_ID}&q={encoded}")
            http_get(base, "/api/health?ordinary=VISIBLE_NON_RESULTS_QUERY")
        logs = output.getvalue()
        self.assertNotIn(raw, logs)
        self.assertNotIn(encoded_plain, logs)
        self.assertNotIn(encoded, logs)
        self.assertNotIn(EVENT_ID, logs)
        self.assertIn("/api/results/search", logs)
        self.assertIn("ordinary=VISIBLE_NON_RESULTS_QUERY", logs)

    def test_vercel_results_logs_omit_raw_and_encoded_query_values(self):
        raw = "RAW_RESULTS_LOG_SENTINEL"
        encoded_plain = "ENCODED_RESULTS_LOG_SENTINEL"
        encoded = urllib.parse.quote(encoded_plain)
        output = io.StringIO()
        with contextlib.redirect_stderr(output), \
                running(vercel_api.handler, cache=FakeCompletedCache(), upstream=FakeUpstream()) as base:
            http_get(base, f"/api/results/search?event_id={EVENT_ID}&q={raw}")
            http_get(base, f"/api/results/search?event_id={EVENT_ID}&q={encoded}")
            http_get(base, "/api/health?ordinary=VISIBLE_NON_RESULTS_QUERY")
        logs = output.getvalue()
        self.assertNotIn(raw, logs)
        self.assertNotIn(encoded_plain, logs)
        self.assertNotIn(encoded, logs)
        self.assertNotIn(EVENT_ID, logs)
        self.assertIn("/api/results/search", logs)
        self.assertIn("ordinary=VISIBLE_NON_RESULTS_QUERY", logs)

    def test_vercel_configuration_includes_results_routes_and_modules(self):
        config = json.loads((Path(__file__).parent / "vercel.json").read_text())
        function = config["functions"]["api/index.py"]
        self.assertEqual(function["maxDuration"], 60)
        for module in ("completed_results.py", "result_analysis.py", "finish_metrics.py", "terrain_model.py"):
            self.assertIn(module, function["includeFiles"])
        routes = json.dumps(config["routes"])
        self.assertIn("results", routes)
        self.assertIn("search", routes)
        self.assertIn("runner", routes)


if __name__ == "__main__":
    unittest.main()
