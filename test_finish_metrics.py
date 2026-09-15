"""Synthetic regression fixtures only; never store participant payloads."""
import copy
import datetime as dt
import json
import unittest
from unittest.mock import patch

import server

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 11, 10, 20, tzinfo=UTC)


def bundle():
    return {
        "event_response": {
            "event": {
                "id": "the-rut-21k-2026", "event_date": "2026-09-11",
                "start_time": "10:00:00", "timezone": "UTC", "course_status": "active",
                "split_names": ["Start", "Turn", "Finish"],
                "split_distances": {"1": {"value": 1}, "2": {"value": 1}},
            },
            "multipliers": [{"split_index": i, "progress_pct": p} for i, p in enumerate([0, .5, 1])],
        },
        "course_response": {"courseMaps": [{
            "eventId": "the-rut-21k-2026",
            "trackPoints": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": .01}, {"lat": 0, "lng": .02}],
        }]},
        "leaderboard": [{"id": 1, "bib": 42, "name": "Synthetic Runner", "city": "Synthetic Town",
                         "last_split_index": 1, "last_split_time": 1000, "estimated_finish_seconds": 3000}],
        "gps_response": {"positions": []}, "upstream_stale": False, "errors": [],
    }


def gps(**updates):
    return {"runner_id": 1, "latitude": 0, "longitude": .015,
            "recorded_at": "2026-09-11T10:19:50Z", **updates}


def view(data=None, now=NOW):
    return server.build_event_view(data or bundle(), now, include_course=True)


class FinishMetricTests(unittest.TestCase):
    def test_estimate_has_distance_observation_and_absolute_eta(self):
        result = view()
        row = result["positions"][0]
        self.assertGreater(result["route_length_m"], 2000)
        self.assertAlmostEqual(row["remaining_m"], result["route_length_m"] * .45, places=3)
        self.assertEqual(row["distance_source"], "SPLIT_ESTIMATE")
        self.assertTrue(row["rank_eligible"])
        self.assertIsNone(row["rank_exclusion"])
        self.assertEqual(row["eta_at"], "2026-09-11T10:50:00Z")
        self.assertEqual(row["observation_at"], "2026-09-11T10:16:40Z")

    def test_gps_projects_segments_not_vertices(self):
        data = bundle()
        data["gps_response"]["positions"] = [gps()]
        result = view(data)
        row = result["positions"][0]
        self.assertAlmostEqual(row["progress"], .75, places=5)
        self.assertAlmostEqual(row["remaining_m"], result["route_length_m"] * .25, places=3)
        self.assertEqual(row["distance_source"], "GPS_MATCHED")
        self.assertTrue(row["rank_eligible"])
        self.assertEqual(row["observation_at"], "2026-09-11T10:19:50Z")
        self.assertEqual(row["eta_at"], "2026-09-11T10:50:00Z")

    def test_remaining_distance_is_along_route_not_crow_distance(self):
        data = bundle()
        data["course_response"]["courseMaps"][0]["trackPoints"] = [
            {"lat": 0, "lng": 0}, {"lat": 0, "lng": .01},
            {"lat": .01, "lng": .01}, {"lat": .01, "lng": 0}, {"lat": 0, "lng": 0},
        ]
        data["leaderboard"][0].update(last_split_index=0, last_split_time=0)
        data["gps_response"]["positions"] = [gps(longitude=.001)]
        result = view(data)
        row = result["positions"][0]
        crow = server.haversine_m({"lat": row["lat"], "lng": row["lng"]}, {"lat": 0, "lng": 0})
        self.assertGreater(row["remaining_m"], crow * 20)
        self.assertTrue(row["rank_eligible"])

    def test_checkpoint_interval_disambiguates_out_and_back(self):
        data = bundle()
        data["course_response"]["courseMaps"][0]["trackPoints"][-1] = {"lat": 0, "lng": 0}
        data["gps_response"]["positions"] = [gps(longitude=.005)]
        result = view(data)
        self.assertAlmostEqual(result["positions"][0]["progress"], .75, places=5)
        data["leaderboard"][0].update(last_split_index=0, last_split_time=0)
        self.assertAlmostEqual(view(data)["positions"][0]["progress"], .25, places=5)

    def test_loop_within_checkpoint_interval_is_ambiguous(self):
        data = bundle()
        data["event_response"]["event"]["split_names"] = ["Start", "Finish"]
        data["event_response"]["multipliers"] = [{"split_index": 0, "progress_pct": 0}, {"split_index": 1, "progress_pct": 1}]
        data["course_response"]["courseMaps"][0]["trackPoints"][-1] = {"lat": 0, "lng": 0}
        data["leaderboard"][0].update(last_split_index=0, last_split_time=0)
        data["gps_response"]["positions"] = [gps(longitude=.005)]
        row = view(data)["positions"][0]
        self.assertFalse(row["rank_eligible"])
        self.assertEqual(row["rank_exclusion"], "GPS_AMBIGUOUS")
        self.assertIsNone(row["remaining_m"])
        self.assertIsNone(row["progress"])
        self.assertEqual(row["lng"], .005)

    def test_off_route_and_wrong_checkpoint_gps_preserve_coordinates_not_rank(self):
        for point in [gps(latitude=1), gps(longitude=.001)]:
            with self.subTest(point=point):
                data = bundle()
                data["gps_response"]["positions"] = [point]
                row = view(data)["positions"][0]
                self.assertEqual(row["source"], "GPS")
                self.assertFalse(row["rank_eligible"])
                self.assertIsNone(row["remaining_m"])
                self.assertEqual(row["lat"], point["latitude"])

    def test_invalid_coordinates_never_become_map_positions_or_estimates(self):
        for field, value in [("latitude", None), ("latitude", ""), ("latitude", True),
                             ("latitude", 91), ("longitude", -181), ("longitude", float("nan")),
                             ("longitude", float("inf"))]:
            with self.subTest(field=field, value=value):
                data = bundle()
                data["gps_response"]["positions"] = [gps(**{field: value})]
                result = view(data)
                self.assertEqual(result["positions"], [])
                self.assertEqual(len(result["runners"]), 1)

    def test_future_missing_naive_and_stale_gps_timestamps_are_not_fresh(self):
        for timestamp in [None, "not a date", "2026-09-11T10:19:50", "2026-09-11T10:20:01Z", "2026-09-11T10:00:00Z"]:
            with self.subTest(timestamp=timestamp):
                data = bundle()
                data["gps_response"]["positions"] = [gps(recorded_at=timestamp)]
                row = view(data)["positions"][0]
                self.assertFalse(row["rank_eligible"])
                self.assertNotEqual(row["freshness"], "LIVE")
                self.assertIsNone(row["eta_at"])

    def test_finished_nonstarters_and_gps_only_are_not_eligible(self):
        cases = [{"finish_time_seconds": 1100}, {"last_split_index": 2}, {"dns": True},
                 {"dq": True}, {"dnq": True}, {"dnf": True}, {"dropped": True},
                 {"last_split_index": None}, {"status": "DNF"}]
        for updates in cases:
            with self.subTest(updates=updates):
                data = bundle()
                data["leaderboard"][0].update(updates)
                data["gps_response"]["positions"] = [gps()]
                self.assertFalse(view(data)["positions"][0]["rank_eligible"])
        data = bundle()
        data["leaderboard"] = []
        data["gps_response"]["positions"] = [gps()]
        self.assertFalse(view(data)["positions"][0]["rank_eligible"])

    def test_actual_event_start_is_required(self):
        for changes in [{"start_time": None, "estimated_start_time": "10:00:00"},
                        {"start_time": "11:00:00"}, {"course_status": "scheduled"}]:
            with self.subTest(changes=changes):
                data = bundle()
                data["event_response"]["event"].update(changes)
                data["gps_response"]["positions"] = [gps()]
                self.assertFalse(view(data)["positions"][0]["rank_eligible"])

    def test_held_and_past_eta_estimates_excluded(self):
        data = bundle()
        row = view(data, NOW + dt.timedelta(hours=2))["positions"][0]
        self.assertTrue(row["estimate_overdue"])
        self.assertFalse(row["rank_eligible"])
        self.assertIsNone(row["eta_at"])

    def test_upstream_failure_holds_estimates_and_excludes_gps(self):
        for changes in [{"upstream_stale": True}, {"errors": ["gps: unavailable"]}]:
            with self.subTest(changes=changes):
                data = bundle()
                data.update(changes)
                first = view(data)["positions"][0]
                later = view(data, NOW + dt.timedelta(minutes=10))["positions"][0]
                self.assertEqual(first["progress"], later["progress"])
                self.assertFalse(first["rank_eligible"])
                self.assertEqual(first["freshness"], "STALE")
                self.assertIsNone(first["eta_at"])
                data["gps_response"]["positions"] = [gps()]
                self.assertFalse(view(data)["positions"][0]["rank_eligible"])

    def test_future_or_unknown_checkpoint_time_does_not_estimate(self):
        for timing in [None, "", True, float("nan"), -1, 1300]:
            with self.subTest(timing=timing):
                data = bundle()
                data["leaderboard"][0]["last_split_time"] = timing
                self.assertEqual(view(data)["positions"], [])
                data["gps_response"]["positions"] = [gps()]
                self.assertFalse(view(data)["positions"][0]["rank_eligible"])

    def test_missing_geometry_keeps_gps_without_distance(self):
        data = bundle()
        data["course_response"] = {}
        data["gps_response"]["positions"] = [gps()]
        result = view(data)
        row = result["positions"][0]
        self.assertIsNone(result["route_length_m"])
        self.assertIsNone(row["remaining_m"])
        self.assertFalse(row["rank_eligible"])
        self.assertEqual(row["lng"], .015)

    def test_missing_checkpoint_geometry_does_not_invent_equal_splits(self):
        data = bundle()
        data["event_response"]["multipliers"] = []
        data["gps_response"]["positions"] = [gps()]
        row = view(data)["positions"][0]
        self.assertFalse(row["rank_eligible"])
        self.assertIsNone(row["remaining_m"])

    def test_wrong_event_or_multiple_courses_are_not_guessed(self):
        for mode in ["wrong", "multiple"]:
            data = bundle()
            maps = data["course_response"]["courseMaps"]
            if mode == "wrong":
                maps[0]["eventId"] = "other-event"
            else:
                maps.append(copy.deepcopy(maps[0]))
            data["gps_response"]["positions"] = [gps()]
            self.assertFalse(view(data)["positions"][0]["rank_eligible"])

    def test_no_supported_gps_timing_or_past_eta_means_no_eta(self):
        data = bundle()
        data["gps_response"]["positions"] = [gps()]
        data["leaderboard"][0].update(last_split_index=0, last_split_time=0, estimated_finish_seconds=None, goal_time_seconds=3000)
        data["gps_response"]["positions"][0]["longitude"] = .005
        row = view(data)["positions"][0]
        self.assertTrue(row["rank_eligible"])
        self.assertIsNone(row["eta_at"])
        data["leaderboard"][0].update(last_split_index=1, last_split_time=1000, estimated_finish_seconds=1100)
        data["gps_response"]["positions"][0]["longitude"] = .015
        row = view(data)["positions"][0]
        self.assertTrue(row["rank_eligible"])
        self.assertIsNone(row["eta_at"])


    def test_observed_pace_takes_precedence_over_a_goal(self):
        data = bundle()
        data["leaderboard"][0].update(estimated_finish_seconds=None, goal_time_seconds=9000)
        row = view(data)["positions"][0]
        self.assertEqual(row["eta_at"], "2026-09-11T10:33:20Z")

    def test_near_finish_checkpoint_estimate_never_moves_backward(self):
        data = bundle()
        data["event_response"]["multipliers"][1]["progress_pct"] = .999
        row = view(data)["positions"][0]
        self.assertGreaterEqual(row["progress"], .999)

    def test_overflowing_projection_is_not_ranked(self):
        data = bundle()
        data["leaderboard"][0]["estimated_finish_seconds"] = 1e308
        self.assertEqual(view(data)["positions"], [])

    def test_capped_estimate_is_held_before_the_deadline_and_not_ranked(self):
        row = view(bundle(), NOW + dt.timedelta(seconds=1781))["positions"][0]
        self.assertFalse(row["estimate_overdue"])
        self.assertTrue(row["estimate_held"])
        self.assertFalse(row["rank_eligible"])
        self.assertEqual(row["rank_exclusion"], "ESTIMATE_HELD")

    def test_gps_before_checkpoint_or_with_bad_accuracy_is_excluded(self):
        for updates in [{"recorded_at": "2026-09-11T10:19:00Z"}, {"accuracy": -1},
                        {"accuracy": 200}, {"accuracy": "bad"}, {"accuracy": True}]:
            with self.subTest(updates=updates):
                data = bundle()
                data["leaderboard"][0]["last_split_time"] = 1190
                data["gps_response"]["positions"] = [gps(**updates)]
                row = view(data)["positions"][0]
                self.assertFalse(row["rank_eligible"])

    def test_invalid_route_points_do_not_bridge_a_gap(self):
        data = bundle()
        data["course_response"]["courseMaps"][0]["trackPoints"][1]["lat"] = None
        data["gps_response"]["positions"] = [gps()]
        result = view(data)
        self.assertIsNone(result["route_length_m"])
        self.assertFalse(result["positions"][0]["rank_eligible"])

    def test_null_progress_and_bad_split_mapping_are_not_zero(self):
        for progress in [None, "", True, -1, 1.5, float("inf")]:
            data = bundle()
            data["event_response"]["multipliers"][1]["progress_pct"] = progress
            data["gps_response"]["positions"] = [gps()]
            row = view(data)["positions"][0]
            self.assertIsNone(row["progress"])
            self.assertIsNone(row["remaining_m"])
            self.assertFalse(row["rank_eligible"])

    def test_gps_start_finish_overlap_uses_checkpoint_interval(self):
        data = bundle()
        data["course_response"]["courseMaps"][0]["trackPoints"][-1] = {"lat": 0, "lng": 0}
        data["gps_response"]["positions"] = [gps(longitude=0)]
        self.assertAlmostEqual(view(data)["positions"][0]["progress"], 1.0)
        data["leaderboard"][0].update(last_split_index=0, last_split_time=0)
        self.assertAlmostEqual(view(data)["positions"][0]["progress"], 0.0)

    def test_expired_source_metadata_does_not_restart_estimation_clock(self):
        data = bundle()
        data["source_freshness"] = {"leaderboard": {
            "fetched_at": "2026-09-11T10:19:00Z", "ttl_seconds": 30, "stale": False, "error": None}}
        first = view(data)["positions"][0]
        later = view(data, NOW + dt.timedelta(minutes=1))["positions"][0]
        self.assertFalse(first["rank_eligible"])
        self.assertEqual(first["progress"], later["progress"])
        self.assertTrue(view(data)["source_freshness"]["leaderboard"]["stale"])


class PrivacyAndSourceTests(unittest.TestCase):
    def test_identifiers_are_not_rounded_through_floats(self):
        value = 9007199254740993
        self.assertEqual(server.as_int(value), value)
        self.assertEqual(server.as_int(str(value)), value)
        for invalid in [True, float("inf"), float("nan"), "1.5", "1.0", ""]:
            self.assertIsNone(server.as_int(invalid))

    def test_anonymity_is_union_of_all_matching_sources_including_roster(self):
        for source, flag in [("gps", "is_anonymous"), ("gps", "athlete_anonymous"),
                             ("leaderboard", "is_anonymous"), ("leaderboard", "athlete_anonymous")]:
            with self.subTest(source=source, flag=flag):
                data = bundle()
                data["gps_response"]["positions"] = [gps(runner_name="GPS Secret")]
                target = data["gps_response"]["positions"][0] if source == "gps" else data["leaderboard"][0]
                target[flag] = True
                result = view(data)
                text = json.dumps(result)
                self.assertNotIn("Synthetic Runner", text)
                self.assertNotIn("GPS Secret", text)
                self.assertNotIn("Synthetic Town", text)
                for row in result["runners"] + result["positions"]:
                    self.assertEqual(row["name"], "Anonymous Participant")
                    self.assertIsNone(row["bib"])

    def test_duplicate_sources_preserve_anonymity_and_latest_gps(self):
        data = bundle()
        data["leaderboard"].append(dict(data["leaderboard"][0], is_anonymous=True))
        data["gps_response"]["positions"] = [gps(), gps(recorded_at="2026-09-11T10:00:00Z", is_anonymous=True)]
        result = view(data)
        self.assertEqual(len(result["runners"]), 1)
        self.assertEqual(len(result["positions"]), 1)
        row = result["positions"][0]
        self.assertEqual(row["name"], "Anonymous Participant")
        self.assertEqual(row["recorded_at"], "2026-09-11T10:19:50Z")

    def test_source_fetch_times_and_degradation_are_exposed(self):
        def fake_upstream(path, ttl):
            if path.endswith("/the-rut-21k-2026") and "/gps/" not in path:
                return bundle()["event_response"], False
            if "/course-maps/" in path:
                return bundle()["course_response"], False
            return {"positions": []}, True
        with patch.object(server, "upstream_json", side_effect=fake_upstream), patch.object(server, "load_leaderboard", return_value=(bundle()["leaderboard"], False)):
            loaded = server.load_event_bundle("the-rut-21k-2026")
        result = view(loaded)
        self.assertTrue(result["upstream_stale"])
        self.assertTrue(result["source_freshness"]["gps"]["stale"])
        self.assertIn("fetched_at", result["source_freshness"]["leaderboard"])

    def test_leaderboard_deduplicates_repeated_pages_and_flags_incomplete(self):
        batch = [{"id": i} for i in range(500)]
        with patch.object(server, "upstream_json", return_value=({"leaderboard": batch}, False)) as request:
            rows, stale = server.load_leaderboard("test-event")
        self.assertEqual(len(rows), 500)
        self.assertTrue(stale)
        self.assertLessEqual(request.call_count, 2)

    def test_pagination_uses_server_page_size_when_has_more_is_explicit(self):
        pages = [({"leaderboard": [{"id": 1}, {"id": 2}], "total": 3, "has_more": True}, False),
                 ({"leaderboard": [{"id": 2}, {"id": 3}], "total": 3, "has_more": False}, False)]
        with patch.object(server, "upstream_json", side_effect=pages) as request:
            rows, stale = server.load_leaderboard("test-event")
        self.assertEqual(len(rows), 3)
        self.assertFalse(stale)
        self.assertIn("offset=2", request.call_args_list[1].args[0])

    def test_partial_pagination_error_preserves_privacy_evidence_but_is_stale(self):
        batch = [{"id": i, "is_anonymous": True} for i in range(500)]
        with patch.object(server, "upstream_json", side_effect=[({"leaderboard": batch}, False), OSError("offline")]):
            rows, stale = server.load_leaderboard("test-event")
        self.assertEqual(len(rows), 500)
        self.assertTrue(stale)
        self.assertTrue(all(row["is_anonymous"] for row in rows))


class SourceIntegrityRegressionTests(unittest.TestCase):
    def test_real_cache_fallback_exposes_only_sanitized_source_errors(self):
        cache = server.JsonCache()
        event_id = "the-rut-21k-2026"
        payloads = {
            f"/events/{event_id}": bundle()["event_response"],
            f"/course-maps/event/{event_id}?include=points": bundle()["course_response"],
            f"/gps/locations/{event_id}": {"positions": [gps()]},
            f"/events/{event_id}/leaderboard?limit=500&offset=0": {"leaderboard": bundle()["leaderboard"]},
        }
        for path, payload in payloads.items():
            cache._entries[server.UPSTREAM + path] = server.CacheEntry(payload, -10000, NOW.timestamp())
        with patch.object(server, "CACHE", cache), patch.object(server.time, "monotonic", return_value=0), patch.object(
                server.urllib.request, "urlopen", side_effect=OSError("PRIVATE UPSTREAM BODY")):
            loaded = server.load_event_bundle(event_id)
        self.assertEqual(len(loaded["errors"]), 4)
        for source, state in loaded["source_freshness"].items():
            self.assertTrue(state["stale"])
            self.assertEqual(state["error"], f"{source}: stale or incomplete")
            self.assertIn(state["error"], loaded["errors"])
        result = view(loaded)
        self.assertTrue(result["upstream_stale"])
        self.assertFalse(result["positions"][0]["rank_eligible"])
        self.assertNotIn("PRIVATE UPSTREAM BODY", json.dumps(result))

    def test_incomplete_pagination_is_reported_in_bundle_errors(self):
        with patch.object(server, "upstream_json", return_value=({}, False)), patch.object(
                server, "load_leaderboard", return_value=(bundle()["leaderboard"], True)):
            loaded = server.load_event_bundle("the-rut-21k-2026")
        self.assertEqual(loaded["errors"], ["leaderboard: stale or incomplete"])
        self.assertEqual(loaded["source_freshness"]["leaderboard"]["error"], loaded["errors"][0])

    def test_explicit_more_continues_past_total_and_preserves_anonymity(self):
        pages = [({"leaderboard": [{"id": 1}], "total": 1, "has_more": True}, False),
                 ({"leaderboard": [{"id": 1, "is_anonymous": True}, {"id": 2}],
                   "total": 2, "has_more": False}, False)]
        with patch.object(server, "upstream_json", side_effect=pages) as request:
            rows, stale = server.load_leaderboard("test-event")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["is_anonymous"])
        self.assertTrue(stale)

    def test_total_assertions_include_zero_excess_and_terminal_deficit(self):
        for metadata in [{"total": 0}, {"total": 1}, {"total_count": 1},
                         {"total": 3, "has_more": False}, {"total": 2, "total_count": 3}]:
            with self.subTest(metadata=metadata), patch.object(server, "upstream_json", return_value=(
                    {"leaderboard": [{"id": 1}, {"id": 2}], **metadata}, False)):
                rows, stale = server.load_leaderboard("test-event")
                self.assertEqual(len(rows), 2)
                self.assertTrue(stale)

    def test_total_assertion_survives_later_missing_metadata(self):
        pages = [({"leaderboard": [{"id": 1}], "total": 3, "has_more": True}, False),
                 ({"leaderboard": [{"id": 2}], "has_more": False}, False)]
        with patch.object(server, "upstream_json", side_effect=pages):
            rows, stale = server.load_leaderboard("test-event")
        self.assertEqual(len(rows), 2)
        self.assertTrue(stale)

    def test_no_metadata_pagination_continues_until_short_page(self):
        pages = [({"status": "ok", "leaderboard": [{"id": i} for i in range(500)]}, False),
                 ({"status": "ok", "leaderboard": [{"id": 500}]}, False)]
        with patch.object(server, "upstream_json", side_effect=pages) as request:
            rows, stale = server.load_leaderboard("test-event")
        self.assertEqual(request.call_count, 2)
        self.assertIn("offset=500", request.call_args_list[1].args[0])
        self.assertEqual(len(rows), 501)
        self.assertFalse(stale)

    def test_metadata_has_bounded_assembly_grace_not_an_outage(self):
        for ttl in [server.GPS_TTL_SECONDS, server.LEADERBOARD_TTL_SECONDS, server.COURSE_TTL_SECONDS]:
            for age, stale in [(ttl + 1, False), (ttl + 4, False), (ttl + 5, True), (ttl + 60, True), (-1, True)]:
                with self.subTest(ttl=ttl, age=age):
                    data = bundle()
                    data["source_freshness"] = {"event": {
                        "fetched_at": server.timestamp(NOW - dt.timedelta(seconds=age)),
                        "ttl_seconds": ttl, "stale": False, "error": None}}
                    result = view(data)
                    self.assertEqual(result["upstream_stale"], stale)
                    self.assertEqual(result["positions"][0]["rank_eligible"], not stale)
                    self.assertEqual(result["errors"], [])

    def test_grace_does_not_relax_fallback_or_gps_observation_limits(self):
        for changes in [{"stale": True}, {"error": "event: unavailable"}, {}]:
            for gps_age in [10, 90, 91]:
                with self.subTest(changes=changes, gps_age=gps_age):
                    data = bundle()
                    data["source_freshness"] = {"event": {
                        "fetched_at": server.timestamp(NOW - dt.timedelta(seconds=31)),
                        "ttl_seconds": 30, "stale": False, "error": None, **changes}}
                    data["gps_response"]["positions"] = [gps(recorded_at=server.timestamp(NOW - dt.timedelta(seconds=gps_age)))]
                    result = view(data)
                    self.assertEqual(result["positions"][0]["rank_eligible"], not changes and gps_age <= 90)
                    self.assertEqual(result["upstream_stale"], bool(changes))

    def test_assembly_grace_does_not_extend_cache_refresh_ttl(self):
        cache = server.JsonCache()
        cache._entries["source"] = server.CacheEntry({"old": True}, 0, NOW.timestamp())
        with patch.object(server.time, "monotonic", return_value=30):
            value, stale = cache.get("source", 30, lambda: {"new": True})
        self.assertEqual(value, {"new": True})
        self.assertFalse(stale)


if __name__ == "__main__":
    unittest.main()
