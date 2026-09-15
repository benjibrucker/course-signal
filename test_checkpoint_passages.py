"""Observed-only passage contracts, using synthetic timing data only."""

import copy
import datetime as dt
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import server
from api.index import response_for

START = dt.datetime(2026, 9, 12, 12, tzinfo=dt.timezone.utc)
OFFSET = 300.125
NOW = START + dt.timedelta(seconds=OFFSET + 1900)


def fixture(offset=OFFSET, index=3):
    times = [offset, 400, 1000, 1800]
    runner = dict(id=1, name="Synthetic Runner", bib=9, chip_start_seconds=offset,
                  last_split_index=index, last_split_time=times[index])
    return {
        "event_response": {"event": {"id": "the-rut-21k-2026", "event_date": "2026-09-12",
            "start_time": "06:00:00", "timezone": "America/Denver", "course_status": "active",
            "split_names": ["Start", "Aid A", "Aid B", "Finish"]}},
        "course_response": {}, "gps_response": {"positions": []}, "leaderboard": [runner],
        "history": [{**runner, "splits": [dict(split_index=i, elapsed_seconds=t,
            email="PRIVATE_SPLIT") for i, t in enumerate(times[:index + 1])]}],
        "history_status": "fresh", "history_fetched_at": server.timestamp(NOW),
        "errors": [], "upstream_stale": False,
    }


def view(data=None, now=NOW):
    return server.build_event_view(data if data is not None else fixture(), now, True)


def passage(index, elapsed, offset=OFFSET, clock=True):
    return {"split_index": index, "elapsed_seconds": elapsed,
            "passed_at": server.timestamp(START + dt.timedelta(seconds=offset + elapsed)) if clock else None}


class CheckpointPassageTests(unittest.TestCase):
    def assert_passages(self, row, expected, status="recorded", stale=False):
        self.assertEqual(row.get("checkpoint_passages"), expected)
        self.assertEqual(row.get("checkpoint_passages_status"), status)
        self.assertIs(row.get("checkpoint_passages_stale"), stale)
        for item in row["checkpoint_passages"]:
            self.assertEqual(set(item), {"split_index", "elapsed_seconds", "passed_at"})
            self.assertIs(type(item["split_index"]), int)
            self.assertIsInstance(item["elapsed_seconds"], (int, float))

    def test_complete_finished_history_without_geometry_or_position(self):
        result = view()
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["runners"][0]["status"], "FINISHED")
        self.assert_passages(result["runners"][0], [passage(i, t) for i, t in enumerate([0, 400, 1000, 1800])])
        self.assertEqual(result["split_names"], ["Start", "Aid A", "Aid B", "Finish"])

    def test_zero_and_later_waves_do_not_double_offset_or_subtract_chip_elapsed(self):
        for offset in (0, OFFSET, 14400.5):
            with self.subTest(offset=offset):
                now = START + dt.timedelta(seconds=offset + 1900)
                data = fixture(offset)
                data["history_fetched_at"] = server.timestamp(now)
                row = view(data, now)["runners"][0]
                self.assert_passages(row, [passage(i, t, offset) for i, t in enumerate([0, 400, 1000, 1800])])
                local = server.parse_timestamp(row["checkpoint_passages"][0]["passed_at"]).astimezone(ZoneInfo("America/Denver"))
                self.assertEqual(local, (START + dt.timedelta(seconds=offset)).astimezone(ZoneInfo("America/Denver")))

    def test_start_only_offset_read_is_zero_chip_elapsed_and_never_a_position(self):
        for offset in (0, OFFSET, 14400.5):
            data = fixture(offset, 0)
            now = START + dt.timedelta(seconds=offset)
            data["history_fetched_at"] = server.timestamp(now)
            result = view(data, now)
            self.assertEqual(result["positions"], [])
            self.assert_passages(result["runners"][0], [passage(0, 0, offset)])

    def test_start_only_leaderboard_read_survives_missing_history_as_partial(self):
        data = fixture(index=0)
        data.update(history=[], history_status="unavailable")
        self.assert_passages(view(data)["runners"][0], [passage(0, 0)], "partial")

    def test_canonical_zero_start_read_with_verified_later_wave(self):
        data = fixture(index=0)
        data["leaderboard"][0]["last_split_time"] = 0
        data["history"][0]["last_split_time"] = 0
        data["history"][0]["splits"][0]["elapsed_seconds"] = 0
        self.assert_passages(view(data)["runners"][0], [passage(0, 0)])

    def test_sparse_history_exposes_only_observed_rows(self):
        data = fixture()
        del data["history"][0]["splits"][2]
        self.assert_passages(view(data)["runners"][0], [passage(0, 0), passage(1, 400), passage(3, 1800)], "partial")

    def test_absent_start_read_is_not_the_estimators_virtual_anchor(self):
        data = fixture()
        del data["history"][0]["splits"][0]
        self.assert_passages(view(data)["runners"][0], [passage(1, 400), passage(2, 1000), passage(3, 1800)], "partial")

    def test_latest_only_and_missing_history_never_fill_earlier_checkpoints(self):
        for history in ([], None, [{**fixture()["history"][0], "splits": [dict(split_index=3, elapsed_seconds=1800)]}]):
            data = fixture()
            data["history"] = history
            self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial")

    def test_unknown_or_invalid_offset_retains_latest_elapsed_without_clock_or_start(self):
        for offset in (None, "", "unknown", -1, True, float("nan"), float("inf"), {}, []):
            with self.subTest(offset=offset):
                data = fixture()
                data["leaderboard"][0]["chip_start_seconds"] = offset
                data["leaderboard"][0]["gun_start_seconds"] = 0
                self.assert_passages(view(data)["runners"][0], [passage(3, 1800, clock=False)], "partial")
                data["leaderboard"][0].update(last_split_index=0, last_split_time=0)
                self.assert_passages(view(data)["runners"][0], [], "unavailable")
        data = fixture()
        del data["leaderboard"][0]["chip_start_seconds"]
        self.assert_passages(view(data)["runners"][0], [passage(3, 1800, clock=False)], "partial")

    def test_missing_actual_clock_does_not_use_scheduled_start(self):
        for start in (None, "invalid"):
            data = fixture()
            data["event_response"]["event"].update(start_time=start, estimated_start_time="06:00:00")
            self.assert_passages(view(data)["runners"][0], [passage(i, t, clock=False) for i, t in [(1, 400), (2, 1000), (3, 1800)]], "partial")
            data = fixture(index=0)
            data["event_response"]["event"].update(start_time=start, estimated_start_time="06:00:00")
            self.assert_passages(view(data)["runners"][0], [], "unavailable")

    def test_dns_registered_and_gps_only_have_no_invented_start(self):
        for status in ("DNS", "REGISTERED"):
            data = fixture(index=0)
            data["leaderboard"][0]["status"] = status
            self.assert_passages(view(data)["runners"][0], [], "unavailable")
        data = fixture()
        data["leaderboard"] = []
        data["history"] = []
        data["gps_response"]["positions"] = [dict(runner_id=1, latitude=45, longitude=-111,
                                                   recorded_at=server.timestamp(NOW))]
        result = view(data)
        self.assert_passages(result["runners"][0], [], "unavailable")
        self.assert_passages(result["positions"][0], [], "unavailable")

    def test_dnf_and_gps_rows_keep_same_roster_history_independent_of_eligibility(self):
        data = fixture(index=2)
        data["leaderboard"][0]["dnf"] = True
        data["gps_response"]["positions"] = [dict(runner_id=1, latitude=45, longitude=-111,
                                                   recorded_at=server.timestamp(NOW))]
        result = view(data)
        for row in (result["runners"][0], result["positions"][0]):
            self.assertEqual(row["status"], "DROPPED")
            self.assert_passages(row, [passage(0, 0), passage(1, 400), passage(2, 1000)])
        self.assertFalse(result["positions"][0]["rank_eligible"])
        data["gps_response"]["positions"][0]["latitude"] = None
        self.assertEqual(view(data)["positions"], [])
        self.assert_passages(view(data)["runners"][0], [passage(0, 0), passage(1, 400), passage(2, 1000)])

    def test_future_event_runner_start_and_latest_checkpoint_fail_closed(self):
        for changes in ({"chip_start_seconds": 5000}, {"chip_start_seconds": 1e308}, {"last_split_time": 1901}):
            data = fixture()
            data["leaderboard"][0].update(changes)
            self.assert_passages(view(data)["runners"][0], [], "unavailable")
        data = fixture()
        data["event_response"]["event"]["start_time"] = "20:00:00"
        self.assert_passages(view(data)["runners"][0], [], "unavailable")
        data["leaderboard"][0]["chip_start_seconds"] = None
        self.assert_passages(view(data)["runners"][0], [], "unavailable")

    def test_malformed_latest_and_bounds_never_expose_history(self):
        for changes in ({"last_split_index": -1}, {"last_split_index": 4}, {"last_split_index": 1.5},
                        {"last_split_index": True}, {"last_split_index": None}, {"last_split_time": 0},
                        {"last_split_time": -1}, {"last_split_time": True}, {"last_split_time": float("nan")},
                        {"last_split_time": float("inf")}, {"last_split_time": 1e308},
                        {"last_split_index": 0, "last_split_time": 15}):
            with self.subTest(changes=changes):
                data = fixture()
                data["leaderboard"][0].update(changes)
                self.assert_passages(view(data)["runners"][0], [], "unavailable")
        data = fixture()
        data["event_response"]["event"]["split_names"] = []
        self.assert_passages(view(data)["runners"][0], [], "unavailable")

    def test_bulk_id_offset_and_latest_summary_must_match_leaderboard(self):
        for changes in ({"id": 2}, {"id": None}, {"chip_start_seconds": None}, {"chip_start_seconds": OFFSET + .02},
                        {"last_split_index": 2}, {"last_split_time": 1800.02}):
            data = fixture()
            data["history"][0].update(changes)
            self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial")

    def test_bad_duplicate_nonmonotone_future_or_conflicting_history_falls_back(self):
        good = fixture()["history"][0]["splits"]
        for splits in (None, {}, [None], good + [dict(split_index=3, elapsed_seconds=1800)],
                       good + [dict(split_index=4, elapsed_seconds=1850)],
                       [good[1], good[0], *good[2:]],
                       [good[0], dict(split_index=1, elapsed_seconds=1200), *good[2:]],
                       [good[0], dict(split_index=1, elapsed_seconds=0), *good[2:]],
                       [good[0], dict(split_index=1, elapsed_seconds=float("nan")), *good[2:]],
                       [good[0], dict(split_index=True, elapsed_seconds=400), *good[2:]],
                       [dict(split_index=0, elapsed_seconds=42), *good[1:]],
                       [*good[:3], dict(split_index=3, elapsed_seconds=1901)],
                       [*good[:3], dict(split_index=3, elapsed_seconds=1799)], good[:3]):
            with self.subTest(splits=splits):
                data = fixture()
                data["history"][0]["splits"] = splits
                self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial")

    def test_stale_history_retains_only_latest_and_is_explicitly_stale(self):
        for fetched_at in (None, "bad", server.timestamp(NOW - dt.timedelta(seconds=60)),
                           server.timestamp(NOW + dt.timedelta(seconds=1))):
            data = fixture()
            data["history_fetched_at"] = fetched_at
            self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial", True)
        data = fixture()
        data["history_status"] = "stale"
        self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial", True)
        self.assertFalse(view(data)["upstream_stale"])

    def test_unavailable_or_malformed_bulk_never_becomes_recorded(self):
        data = fixture()
        data["history_status"] = "unavailable"
        self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial")
        for rows in ([*fixture()["history"], *fixture()["history"]], [*fixture()["history"], None]):
            data = fixture()
            data["history"] = rows
            self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial")

    def test_core_timing_staleness_cannot_label_history_complete(self):
        data = fixture()
        data["source_freshness"] = {"leaderboard": {"fetched_at": server.timestamp(NOW),
            "ttl_seconds": 30, "stale": True, "error": "leaderboard: stale or incomplete"}}
        self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial", True)

    def test_unrelated_gps_or_course_staleness_does_not_age_recorded_passages(self):
        data = fixture()
        data["source_freshness"] = {"gps": {"fetched_at": server.timestamp(NOW),
            "ttl_seconds": 15, "stale": True, "error": "gps: unavailable"}}
        data["upstream_stale"] = True
        self.assert_passages(view(data)["runners"][0], [passage(i, t) for i, t in enumerate([0, 400, 1000, 1800])])

    def test_privacy_union_allowlist_and_serialization_through_api(self):
        data = fixture(index=2)
        data["history"][0].update(athlete_anonymous=True, email="PRIVATE_IDENTITY", phone="PRIVATE_PHONE")
        data["gps_response"]["positions"] = [dict(runner_id=1, latitude=45, longitude=-111,
                                                   recorded_at=server.timestamp(NOW))]
        original = copy.deepcopy(data)
        result = view(data)
        payload = {"version": server.APP_VERSION, "events": [result], "summary": {"errors": 0}}
        with patch.object(server, "build_payload", return_value=payload):
            status, public = response_for("live")
        self.assertEqual(status, 200)
        serialized = server.json_bytes(public).decode()
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("Synthetic Runner", serialized)
        self.assertNotIn('"splits"', serialized)
        decoded = json.loads(serialized)
        for row in [*decoded["events"][0]["runners"], *decoded["events"][0]["positions"]]:
            self.assertEqual(row["name"], "Anonymous Participant")
            self.assertIsNone(row["bib"])
            self.assert_passages(row, [passage(0, 0), passage(1, 400), passage(2, 1000)])
        self.assertEqual(data, original)

    def test_disabled_terrain_still_fetches_optional_history_without_model_or_ttl_change(self):
        data = fixture(index=2)
        calls = []
        def fetch(path, ttl):
            calls.append((path, ttl))
            if path.endswith("/results"):
                return {"results": data["history"], "total": 1}, False
            if "leaderboard?" in path:
                return {"leaderboard": data["leaderboard"], "has_more": False}, False
            if path.startswith("/gps/"):
                return data["gps_response"], False
            if path.startswith("/course-maps/"):
                return data["course_response"], False
            return data["event_response"], False
        with patch.object(server, "TERRAIN_PILOT_ENABLED", False), patch.object(
                server, "upstream_json", side_effect=fetch), patch.object(
                server.CACHE, "source_fetched_at", return_value=server.timestamp(NOW)), patch.object(
                server, "predict", side_effect=AssertionError("disabled model called")):
            result = view(server.load_event_bundle("the-rut-21k-2026"))
        self.assert_passages(result["runners"][0], [passage(0, 0), passage(1, 400), passage(2, 1000)])
        self.assertFalse(result["estimator"]["enabled"])
        self.assertEqual(calls[0], ("/events/the-rut-21k-2026/results", 60))
        self.assertEqual([ttl for path, ttl in calls if not path.endswith("/results")], [30, 3600, 15, 30])

    def test_stale_signal_survives_duplicate_bulk_rejection(self):
        data = fixture()
        data["history_status"] = "stale"
        data["history"].append(copy.deepcopy(data["history"][0]))
        self.assert_passages(view(data)["runners"][0], [passage(3, 1800)], "partial", True)

    def test_negative_bulk_summary_cannot_hide_inside_matching_tolerance(self):
        data = fixture(offset=0, index=0)
        data["history"][0]["last_split_time"] = -.005
        self.assert_passages(view(data)["runners"][0], [passage(0, 0, offset=0)], "partial")

    def test_estimated_positions_copy_roster_passages_without_changing_model(self):
        from test_terrain_integration import fixture as terrain_fixture, NOW as terrain_now
        data = terrain_fixture()
        result = view(data, terrain_now)
        row = result["positions"][0]
        self.assertEqual(row["source"], "ESTIMATED")
        self.assertTrue(row["rank_eligible"])
        self.assertEqual(row["estimate_model"], "terrain-pilot-v1")
        self.assertEqual(row["pace_segments_used"], 3)
        self.assertEqual(row["checkpoint_passages"], result["runners"][0]["checkpoint_passages"])
        self.assertEqual(row["checkpoint_passages_status"], "recorded")

    def test_only_named_official_chart_is_public_not_arbitrary_root_images(self):
        import tempfile
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = b"synthetic public PNG fixture"
            (root / "rut-2026-aid-chart.png").write_bytes(body)
            (root / "private.png").write_bytes(b"synthetic private fixture")
            with patch.object(server, "APP_ROOT", root.resolve()):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.RutHandler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    with urllib.request.urlopen(base + "/rut-2026-aid-chart.png") as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["Content-Type"], "image/png")
                        self.assertEqual(response.read(), body)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base + "/private.png")
                    self.assertEqual(error.exception.code, 404)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join()

    def test_api_version_is_160(self):
        self.assertEqual(response_for("health")[1]["version"], "1.6.0")


if __name__ == "__main__":
    unittest.main()
