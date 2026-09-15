"""Synthetic future-only schedules; never feed predictions into observations."""
import copy
import dataclasses
import datetime as dt
import json
import unittest
from unittest.mock import patch

import build_static
import server
import terrain_model
from api.index import response_for
from test_terrain_integration import fixture, view, NOW, START, OFFSET


class CheckpointForecastTests(unittest.TestCase):
    def assert_schedule(self, row, now=NOW):
        schedule = row.get("checkpoint_forecasts")
        self.assertIsInstance(schedule, list)
        self.assertEqual([x["split_index"] for x in schedule], list(range(row["last_split_index"] + 1, 6)))
        self.assertEqual(row["checkpoint_forecast_basis"], row["eta_basis"])
        self.assertEqual(schedule[0]["estimated_at"], row["next_checkpoint_at"])
        self.assertEqual(schedule[-1]["estimated_at"], row["eta_at"])
        previous = now
        for item in schedule:
            self.assertEqual(set(item), {"split_index", "estimated_at"})
            self.assertIs(type(item["split_index"]), int)
            self.assertTrue(item["estimated_at"].endswith("Z"))
            clock = server.parse_timestamp(item["estimated_at"])
            self.assertGreater(clock, previous)
            previous = clock
        return schedule

    def assert_empty(self, result):
        for row in result["positions"]:
            self.assertEqual(row.get("checkpoint_forecasts"), [])
            self.assertIsNone(row.get("checkpoint_forecast_basis"))

    def test_terrain_schedule_is_frozen_and_reuses_first_last_predictions(self):
        profile = terrain_model.build_profile(list(range(0, 2001, 100)), [1000 + abs(10 - i) * 12 for i in range(21)])
        for last, times in ((1, [(0, 0), (1, 400)]), (3, [(0, 0), (1, 400), (3, 1800)]),
                            (4, [(0, 0), (1, 400), (3, 1800), (4, 2500)])):
            prediction = terrain_model.predict(profile, [0, .2, .4, .6, .8, 1], times, last, times[-1][1], times[-1][1] + 1)
            schedule = getattr(prediction, "checkpoint_seconds", None)
            self.assertIsInstance(schedule, tuple)
            self.assertEqual([i for i, _ in schedule], list(range(last + 1, 6)))
            self.assertEqual(schedule[0][1], prediction.next_checkpoint_seconds)
            self.assertEqual(schedule[-1][1], prediction.finish_seconds)
            self.assertTrue(all(a[1] < b[1] for a, b in zip(schedule, schedule[1:])))
            effort_at = lambda i: terrain_model._interpolate(profile.cumulative_m, profile.cumulative_effort_m, i / 5 * 2000)
            last_time = times[-1][1]
            pace = (prediction.finish_seconds - last_time) / (effort_at(5) - effort_at(last))
            for i, seconds in schedule:
                self.assertAlmostEqual(seconds, last_time + pace * (effort_at(i) - effort_at(last)), places=9)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                prediction.checkpoint_seconds = ()

    def test_terrain_all_remaining_and_no_legacy_hill_doublecount(self):
        data = fixture()
        with patch.object(server, "segment_weights", side_effect=AssertionError("double-counted hills")):
            row = view(data)["positions"][0]
            self.assert_schedule(row)
        for multiplier in data["event_response"]["multipliers"]:
            multiplier["pace_multiplier"] = .01
        self.assertEqual(row, view(data)["positions"][0])
        self.assertEqual(row["estimate_model"], "terrain-pilot-v1")
        self.assertEqual(row["pace_segments_used"], 3)

    def test_fallback_exact_remaining_segment_weight_proportions(self):
        data = fixture()
        data["event_response"]["event"]["split_distances"] = {"4": {"value": 2}, "5": {"value": 7}}
        data["event_response"]["multipliers"][4]["pace_multiplier"] = 3
        data["event_response"]["multipliers"][5]["pace_multiplier"] = 2
        with patch.object(server, "TERRAIN_PILOT_ENABLED", False):
            result = view(data)
        row = result["positions"][0]
        schedule = self.assert_schedule(row)
        progresses = result["course"]["progress_points"]
        projection = server.projected_progress(data["leaderboard"][0], data["event_response"], progresses, NOW)
        weights = server.segment_weights(data["event_response"], progresses)[4:]
        expected = 1800 + (projection[1] - 1800) * weights[0] / sum(weights)
        chip_start = START + dt.timedelta(seconds=OFFSET)
        self.assertEqual(schedule[0]["estimated_at"], server.timestamp(chip_start + dt.timedelta(seconds=expected)))
        self.assertEqual(row["projected_finish_seconds"], projection[1])
        self.assertEqual(row["progress"], projection[0])
        self.assertNotEqual(expected, projection[1] * .8)
        self.assertEqual(row["eta_basis"], "CHECKPOINT_PACE_CHIP")

    def test_zero_and_later_wave_offsets_are_applied_once(self):
        for enabled in (True, False):
            for offset in (0, 300.125, 14400.5):
                data = fixture()
                for record in (data["leaderboard"][0], data["history"][0]):
                    record["chip_start_seconds"] = offset
                data["history"][0]["splits"][0]["elapsed_seconds"] = offset
                now = START + dt.timedelta(seconds=offset + 1900)
                data["history_fetched_at"] = server.timestamp(now)
                with patch.object(server, "TERRAIN_PILOT_ENABLED", enabled):
                    row = view(data, now)["positions"][0]
                schedule = self.assert_schedule(row, now)
                self.assertEqual(server.parse_timestamp(schedule[-1]["estimated_at"]),
                                 START + dt.timedelta(seconds=offset + row["projected_finish_seconds"]))

    def test_latest_read_updates_future_and_preserves_old_passage_clocks(self):
        data = fixture()
        before = view(data)["positions"][0]
        self.assert_schedule(before)
        for record in (data["leaderboard"][0], data["history"][0]):
            record.update(last_split_index=4, last_split_time=3000)
        data["history"][0]["splits"].append(dict(split_index=4, elapsed_seconds=3000))
        now = START + dt.timedelta(seconds=OFFSET + 3010)
        data["history_fetched_at"] = server.timestamp(now)
        after = view(data, now)["positions"][0]
        self.assert_schedule(after, now)
        self.assertEqual(after["checkpoint_passages"][:-1], before["checkpoint_passages"])
        self.assertNotEqual(after["eta_at"], before["eta_at"])

    def test_skipped_historical_reads_are_not_forecast_or_fabricated(self):
        data = fixture()
        del data["history"][0]["splits"][2]
        del data["history"][0]["splits"][0]
        row = view(data)["positions"][0]
        self.assert_schedule(row)
        self.assertEqual([x["split_index"] for x in row["checkpoint_passages"]], [1, 3])
        self.assertEqual([x["split_index"] for x in row["checkpoint_forecasts"]], [4, 5])

    def test_current_wall_clock_expiration_suppresses_entire_schedule(self):
        for enabled in (True, False):
            data = fixture()
            with patch.object(server, "TERRAIN_PILOT_ENABLED", enabled):
                row = view(data)["positions"][0]
                schedule = self.assert_schedule(row)
                for delta in (0, 1, 30):
                    now = server.parse_timestamp(schedule[0]["estimated_at"]) + dt.timedelta(seconds=delta)
                    data["history_fetched_at"] = server.timestamp(now)
                    self.assert_empty(view(data, now))

    def test_held_degraded_and_expired_core_sources_suppress(self):
        for changes in ({"upstream_stale": True}, {"errors": ["synthetic outage"]},
                        {"source_freshness": {"leaderboard": {"fetched_at": server.timestamp(NOW - dt.timedelta(seconds=90)), "ttl_seconds": 30}}}):
            self.assert_empty(view({**fixture(), **changes}))
        with patch.object(server, "predict", return_value=None), patch.object(server, "projected_progress", return_value=(.799, 3000, False)):
            self.assert_empty(view())

    def test_gps_eligible_keeps_location_but_bad_fixes_have_no_forecast(self):
        data = fixture()
        good = dict(runner_id=1, latitude=0, longitude=.013, recorded_at=server.timestamp(NOW), accuracy=5)
        data["gps_response"]["positions"] = [good]
        row = view(data)["positions"][0]
        self.assert_schedule(row)
        self.assertEqual((row["lat"], row["lng"], row["source"]), (0, .013, "GPS"))
        for change in ({"recorded_at": "invalid"}, {"recorded_at": server.timestamp(NOW - dt.timedelta(seconds=91))},
                       {"recorded_at": server.timestamp(NOW + dt.timedelta(seconds=1))},
                       {"latitude": None}, {"latitude": 1}, {"accuracy": 999}, {"accuracy": "invalid"}):
            data["gps_response"]["positions"] = [{**good, **change}]
            self.assert_empty(view(data))

    def test_terminal_prestart_closed_and_start_only_suppress(self):
        for status in ("FINISHED", "DNS", "DQ", "DNQ", "DROPPED", "REGISTERED"):
            data = fixture()
            data["leaderboard"][0]["status"] = status
            data["gps_response"]["positions"] = [dict(runner_id=1, latitude=0, longitude=.013, recorded_at=server.timestamp(NOW))]
            self.assert_empty(view(data))
        for change in ({"start_time": None, "estimated_start_time": "10:00:00"},
                       {"start_time": "20:00:00"}, {"course_status": "closed"}):
            data = fixture(); data["event_response"]["event"].update(change)
            self.assert_empty(view(data))
        data = fixture(); data["leaderboard"][0].update(last_split_index=0, last_split_time=0, goal_time_seconds=3000)
        self.assert_empty(view(data))

    def test_missing_invalid_offsets_and_legacy_goal_never_forecast(self):
        for offset in (None, -1, True, float("nan"), float("inf"), 1e308, 5000):
            data = fixture(); data["leaderboard"][0]["chip_start_seconds"] = offset
            self.assert_empty(view(data))
        data = fixture(); del data["leaderboard"][0]["chip_start_seconds"]
        data["leaderboard"][0].update(goal_time_seconds=3000, estimated_finish_seconds=3000)
        self.assert_empty(view(data))

    def test_ambiguous_upstream_finish_and_goal_do_not_change_schedule(self):
        for enabled in (True, False):
            with patch.object(server, "TERRAIN_PILOT_ENABLED", enabled):
                before = self.assert_schedule(view()["positions"][0])
                data = fixture(); data["leaderboard"][0].update(estimated_finish_seconds=1e9, goal_time_seconds=1)
                self.assertEqual(before, self.assert_schedule(view(data)["positions"][0]))

    def test_invalid_original_timing_geometry_never_forecasts(self):
        for progresses in ([0, .4, .2, .6, .8, 1], [0, .2, None, .6, .8, 1],
                           [0, .2, float("nan"), .6, .8, 1], [0, .2, .4, .6, .6, 1]):
            with patch.object(server, "split_progresses", return_value=progresses):
                self.assert_empty(view())
        data = fixture(); data["course_response"] = {}
        self.assert_empty(view(data))

    def test_api_snapshot_roundtrip_allowlist_privacy_and_no_mutation(self):
        data = fixture()
        for record in (data["leaderboard"][0], data["history"][0]):
            record.update(is_anonymous=True, email="PRIVATE", checkpoint_forecasts=[{"secret": "PRIVATE"}])
        original = copy.deepcopy(data)
        result = view(data)
        expected = self.assert_schedule(result["positions"][0])
        payload = {"version": server.APP_VERSION, "events": [result], "summary": {"errors": 0}}
        with patch.object(server, "build_payload", return_value=payload):
            status, public = response_for("live")
        self.assertEqual(status, 200)
        for public in (public, build_static.make_live_payload(build_static.sanitize_public(payload))):
            text = server.json_bytes(public).decode()
            self.assertNotIn("PRIVATE", text)
            self.assertNotIn("Synthetic Runner", text)
            self.assertNotIn('"splits"', text)
            restored = json.loads(text)["events"][0]["positions"][0]
            self.assertEqual(restored["checkpoint_forecasts"], expected)
            self.assertEqual(restored["checkpoint_passages"], result["runners"][0]["checkpoint_passages"])
        self.assertEqual(data, original)

    def test_api_version(self):
        self.assertEqual(server.APP_VERSION, "1.6.0")
        self.assertEqual(response_for("health")[1]["version"], "1.6.0")

    def test_early_fallback_includes_each_cumulative_weight_not_equal_steps(self):
        data = fixture()
        for record in (data["leaderboard"][0], data["history"][0]):
            record.update(last_split_index=1, last_split_time=400)
        data["history"][0]["splits"] = data["history"][0]["splits"][:2]
        for i, multiplier in enumerate(data["event_response"]["multipliers"]):
            multiplier["pace_multiplier"] = i + 1
        now = START + dt.timedelta(seconds=OFFSET + 410)
        data["history_fetched_at"] = server.timestamp(now)
        with patch.object(server, "TERRAIN_PILOT_ENABLED", False):
            result = view(data, now)
        row = result["positions"][0]
        schedule = self.assert_schedule(row, now)
        weights = server.segment_weights(data["event_response"], result["course"]["progress_points"])[2:]
        cumulative = 0
        for item, weight in zip(schedule, weights):
            cumulative += weight
            elapsed = 400 + (row["projected_finish_seconds"] - 400) * (cumulative / sum(weights))
            self.assertEqual(item["estimated_at"], server.timestamp(START + dt.timedelta(seconds=OFFSET + elapsed)))

    def test_24_hour_horizon_is_all_or_nothing_without_location_changes(self):
        with patch.object(server, "predict", return_value=None), patch.object(server, "projected_progress", return_value=(.61, 100000, False)):
            row = view()["positions"][0]
        self.assert_empty({"positions": [row]})
        self.assertEqual(row["progress"], .61)
        self.assertEqual(row["projected_finish_seconds"], 100000)
        self.assertTrue(row["rank_eligible"])
        self.assertIsNotNone(row["eta_at"])

    def test_invalid_or_collapsed_schedule_is_rejected_in_full(self):
        from types import SimpleNamespace
        for seconds in (((4, 2400), (5, float("nan"))), ((4, 2400), (5, 2400)),
                        ((4, 2400), (5, 3001)), ((5, 3000),), ((4, 1900), (5, 3000))):
            self.assertEqual(server.checkpoint_forecasts(fixture()["event_response"], [0, .2, .4, .6, .8, 1],
                3, 1800, 3000, SimpleNamespace(checkpoint_seconds=seconds),
                START + dt.timedelta(seconds=OFFSET), NOW), [])


if __name__ == "__main__":
    unittest.main()
