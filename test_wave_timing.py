"""Synthetic chip-clock regressions; no participant payloads."""
import datetime as dt
import unittest
from unittest.mock import patch

import server
from test_finish_metrics import bundle, gps, NOW

START = NOW - dt.timedelta(seconds=1200)


class WaveTimingTests(unittest.TestCase):
    def data(self, offset=0):
        data = bundle()
        data["leaderboard"][0]["chip_start_seconds"] = offset
        return data

    def test_zero_positive_and_late_wave_have_identical_chip_progress(self):
        for offset in (0, 300, 14400):
            with self.subTest(offset=offset):
                data = self.data(offset)
                now = NOW + dt.timedelta(seconds=offset)
                result = server.build_event_view(data, now, False)
                row = result["positions"][0]
                self.assertEqual(result["runners"][0]["chip_start_seconds"], offset)
                self.assertAlmostEqual(row["progress"], .6)
                self.assertEqual(row["observation_at"], server.timestamp(START + dt.timedelta(seconds=offset + 1000)))
                self.assertEqual(row["eta_at"], server.timestamp(START + dt.timedelta(seconds=offset + 2000)))
                self.assertEqual(row["eta_basis"], "CHECKPOINT_PACE_CHIP")
                self.assertEqual(row["estimate_basis"], "CHECKPOINT_PACE_CHIP")
                self.assertEqual(row["projected_finish_seconds"], 2000)
                self.assertTrue(row["rank_eligible"])

    def test_invalid_offsets_never_become_zero_or_predictions(self):
        for offset in (None, "", "bad", -1, True, float("nan"), float("inf"), 1e308):
            with self.subTest(offset=offset):
                data = self.data(offset)
                clean = server.sanitize_runner(data["leaderboard"][0], 2)
                self.assertIn("chip_start_seconds", clean)
                self.assertIsNone(server.checkpoint_observation(clean, START, NOW))
                self.assertIsNone(server.projected_progress(data["leaderboard"][0], data["event_response"], [0, .5, 1], NOW))
                self.assertEqual(server.build_event_view(data, NOW, False)["positions"], [])
                data["gps_response"]["positions"] = [gps()]
                row = server.build_event_view(data, NOW, False)["positions"][0]
                self.assertEqual(row["source"], "GPS")
                self.assertFalse(row["rank_eligible"])
                self.assertIsNone(row["eta_at"])

    def test_future_checkpoint_is_rejected_after_adding_offset(self):
        data = self.data(300)
        self.assertEqual(server.build_event_view(data, NOW, False)["positions"], [])
        clean = server.sanitize_runner(data["leaderboard"][0], 2)
        self.assertIsNone(server.checkpoint_observation(clean, START, NOW))

    def test_no_motion_before_runner_start(self):
        data = self.data(14400)
        runner = data["leaderboard"][0]
        runner.update(last_split_index=0, last_split_time=0, goal_time_seconds=3000)
        self.assertIsNone(server.projected_progress(runner, data["event_response"], [0, .5, 1], NOW))
        self.assertEqual(server.build_event_view(data, NOW, False)["positions"], [])

    def test_start_only_does_not_use_ambiguous_estimate_or_goal(self):
        data = self.data(0)
        data["leaderboard"][0].update(last_split_index=0, last_split_time=0, goal_time_seconds=3000)
        self.assertEqual(server.build_event_view(data, NOW, False)["positions"], [])

    def test_upstream_estimate_and_gun_start_do_not_change_chip_eta(self):
        for estimate in (None, 1100, 3000, 90000, float("nan")):
            data = self.data(300)
            data["leaderboard"][0].update(estimated_finish_seconds=estimate, gun_start_seconds=99999)
            row = server.build_event_view(data, NOW + dt.timedelta(seconds=300), False)["positions"][0]
            self.assertEqual(row["eta_at"], server.timestamp(START + dt.timedelta(seconds=2300)))
            self.assertAlmostEqual(row["progress"], .6)

    def test_gps_before_offset_checkpoint_is_excluded(self):
        data = self.data(100)
        data["gps_response"]["positions"] = [gps(recorded_at=server.timestamp(START + dt.timedelta(seconds=1099)))]
        row = server.build_event_view(data, START + dt.timedelta(seconds=1101), False)["positions"][0]
        self.assertEqual(row["source"], "GPS")
        self.assertEqual(row["rank_exclusion"], "GPS_BEFORE_CHECKPOINT")
        self.assertIsNone(row["eta_at"])

    def test_offset_estimate_still_holds_on_stale_or_overdue(self):
        data = self.data(300)
        data["upstream_stale"] = True
        row = server.build_event_view(data, NOW + dt.timedelta(seconds=300), False)["positions"][0]
        self.assertEqual(row["progress"], .5)
        self.assertTrue(row["estimate_held"])
        self.assertIsNone(row["eta_at"])
        data["upstream_stale"] = False
        row = server.build_event_view(data, NOW + dt.timedelta(hours=2), False)["positions"][0]
        self.assertLess(row["progress"], 1)
        self.assertTrue(row["estimate_held"])
        self.assertFalse(row["rank_eligible"])
        self.assertIsNone(row["eta_at"])

    def test_upstream_missing_offset_is_unknown_not_legacy_zero(self):
        data = bundle()
        def fetch(path, ttl):
            if "leaderboard?" in path:
                return {"leaderboard": data["leaderboard"], "has_more": False}, False
            if path.startswith("/course-maps/"):
                return data["course_response"], False
            if path.startswith("/gps/"):
                return data["gps_response"], False
            return data["event_response"], False
        with patch.object(server, "upstream_json", side_effect=fetch), patch.object(server.CACHE, "source_fetched_at", return_value=server.timestamp(NOW)):
            loaded = server.load_event_bundle("the-rut-21k-2026")
        self.assertIn("chip_start_seconds", loaded["leaderboard"][0])
        self.assertIsNone(loaded["leaderboard"][0]["chip_start_seconds"])
        self.assertEqual(server.build_event_view(loaded, NOW, False)["positions"], [])

    def test_missing_offset_in_known_wave_bundle_is_unknown(self):
        data = self.data(0)
        missing = {**data["leaderboard"][0], "id": 2}
        del missing["chip_start_seconds"]
        data["leaderboard"].append(missing)
        result = server.build_event_view(data, NOW, False)
        self.assertEqual([row["id"] for row in result["positions"]], [1])

    def test_fresh_gps_keeps_precedence_and_chip_eta_origin(self):
        data = self.data(300)
        now = NOW + dt.timedelta(seconds=300)
        data["gps_response"]["positions"] = [gps(recorded_at=server.timestamp(now))]
        row = server.build_event_view(data, now, False)["positions"][0]
        self.assertEqual(row["source"], "GPS")
        self.assertTrue(row["rank_eligible"])
        self.assertEqual(row["lng"], .015)
        self.assertEqual(row["eta_at"], server.timestamp(START + dt.timedelta(seconds=2300)))
        self.assertEqual(row["eta_basis"], "CHECKPOINT_PACE_CHIP")

    def test_checkpoint_at_now_has_no_extrapolated_motion(self):
        data = self.data("300")
        row = server.build_event_view(data, START + dt.timedelta(seconds=1300), False)["positions"][0]
        self.assertEqual(row["progress"], .5)
        self.assertEqual(row["age_seconds"], 0)

    def test_legacy_in_memory_fixture_remains_compatible(self):
        row = server.build_event_view(bundle(), NOW, False)["positions"][0]
        self.assertEqual(row["eta_at"], "2026-09-11T10:50:00Z")


if __name__ == "__main__":
    unittest.main()
