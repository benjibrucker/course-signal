import datetime as dt
import math
import unittest

import build_static
import server


class GeometryTests(unittest.TestCase):
    def test_route_interpolation_finds_midpoint(self):
        track = [{"lat": 0.0, "lng": 0.0}, {"lat": 0.0, "lng": 1.0}]
        cumulative, total = server.route_distances(track)
        point = server.interpolate_route(track, cumulative, total, 0.5)
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point[0], 0.0, places=6)
        self.assertAlmostEqual(point[1], 0.5, places=6)

    def test_nearest_split_progress_uses_route_distance(self):
        track = [
            {"lat": 0.0, "lng": 0.0},
            {"lat": 0.0, "lng": 1.0},
            {"lat": 0.0, "lng": 2.0},
        ]
        cumulative, total = server.route_distances(track)
        progress = server.nearest_route_progress({"lat": 0.0, "lng": 1.0}, track, cumulative, total)
        self.assertAlmostEqual(progress, 0.5, places=3)


class EstimationTests(unittest.TestCase):
    def setUp(self):
        self.event_response = {
            "event": {
                "id": "the-rut-21k-2026",
                "event_date": "2026-09-11T12:00:00.000Z",
                "start_time": "10:00:00",
                "timezone": "UTC",
                "split_names": ["Start", "Half", "Finish"],
                "split_distances": {
                    "1": {"value": 5, "unit": "mi"},
                    "2": {"value": 5, "unit": "mi"},
                },
            },
            "multipliers": [
                {"split_index": 0, "progress_pct": 0.0, "pace_multiplier": 1.0},
                {"split_index": 1, "progress_pct": 0.5, "pace_multiplier": 1.0},
                {"split_index": 2, "progress_pct": 1.0, "pace_multiplier": 1.0},
            ],
        }

    def test_projection_moves_forward_from_last_checkpoint(self):
        runner = {
            "last_split_index": 1,
            "last_split_time": 1000,
            "estimated_finish_seconds": 3000,
            "finish_time_seconds": None,
            "dns": False,
            "dropped": False,
        }
        now = dt.datetime(2026, 9, 11, 10, 20, tzinfo=dt.timezone.utc)
        result = server.projected_progress(runner, self.event_response, [0.0, 0.5, 1.0], now)
        self.assertIsNotNone(result)
        progress, projected_finish, overdue = result
        self.assertGreater(progress, 0.5)
        self.assertLess(progress, 1.0)
        self.assertEqual(projected_finish, 3000)
        self.assertFalse(overdue)

    def test_overdue_projection_stops_before_next_checkpoint(self):
        runner = {
            "last_split_index": 0,
            "last_split_time": 0,
            "estimated_finish_seconds": 3000,
            "finish_time_seconds": None,
        }
        now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone.utc)
        result = server.projected_progress(runner, self.event_response, [0.0, 0.5, 1.0], now)
        self.assertIsNotNone(result)
        progress, _, overdue = result
        self.assertTrue(overdue)
        self.assertGreater(progress, 0.49)
        self.assertLess(progress, 0.5)

    def test_projection_does_not_place_finished_runner(self):
        runner = {
            "last_split_index": 2,
            "last_split_time": 2500,
            "finish_time_seconds": 2500,
        }
        now = dt.datetime(2026, 9, 11, 10, 50, tzinfo=dt.timezone.utc)
        result = server.projected_progress(runner, self.event_response, [0.0, 0.5, 1.0], now)
        self.assertIsNone(result)

    def test_projection_does_not_place_runner_before_start(self):
        runner = {
            "last_split_index": 0,
            "last_split_time": 0,
            "goal_time_seconds": 3000,
        }
        now = dt.datetime(2026, 9, 11, 9, 59, tzinfo=dt.timezone.utc)
        result = server.projected_progress(runner, self.event_response, [0.0, 0.5, 1.0], now)
        self.assertIsNone(result)


class PrivacyAndMergeTests(unittest.TestCase):
    def test_sanitizer_drops_private_upstream_fields(self):
        raw = {
            "id": 10,
            "bib": 22,
            "name": "Runner Name",
            "email": "private@example.com",
            "phone": "555-0100",
            "dob": "2000-01-01",
            "finish_time_seconds": None,
            "last_split_index": 1,
        }
        clean = server.sanitize_runner(raw, finish_index=4)
        self.assertEqual(clean["name"], "Runner Name")
        self.assertNotIn("email", clean)
        self.assertNotIn("phone", clean)
        self.assertNotIn("dob", clean)

    def test_anonymous_name_is_preserved_as_anonymous(self):
        clean = server.sanitize_runner(
            {"id": 10, "name": "Hidden Name", "is_anonymous": True, "last_split_index": None},
            finish_index=4,
        )
        self.assertEqual(clean["name"], "Anonymous Participant")

    def test_fresh_gps_overrides_estimate(self):
        now = dt.datetime(2026, 9, 11, 10, 20, tzinfo=dt.timezone.utc)
        bundle = {
            "event_response": {
                "event": {
                    "id": "the-rut-21k-2026",
                    "display_name": "21K",
                    "event_date": "2026-09-11T12:00:00.000Z",
                    "start_time": "10:00:00",
                    "timezone": "UTC",
                    "course_status": "active",
                    "split_names": ["Start", "Half", "Finish"],
                    "split_distances": {
                        "1": {"value": 5, "unit": "mi"},
                        "2": {"value": 5, "unit": "mi"},
                    },
                },
                "multipliers": [
                    {"split_index": 0, "progress_pct": 0.0, "pace_multiplier": 1.0},
                    {"split_index": 1, "progress_pct": 0.5, "pace_multiplier": 1.0},
                    {"split_index": 2, "progress_pct": 1.0, "pace_multiplier": 1.0},
                ],
            },
            "course_response": {
                "courseMaps": [
                    {
                        "color": "#ffffff",
                        "trackPoints": [
                            {"lat": 45.0, "lng": -111.0},
                            {"lat": 45.1, "lng": -111.1},
                        ],
                        "splitPoints": [
                            {"lat": 45.0, "lng": -111.0},
                            {"lat": 45.05, "lng": -111.05},
                            {"lat": 45.1, "lng": -111.1},
                        ],
                    }
                ]
            },
            "gps_response": {
                "positions": [
                    {
                        "runner_id": 10,
                        "latitude": 45.02,
                        "longitude": -111.02,
                        "recorded_at": "2026-09-11T10:19:50Z",
                    }
                ]
            },
            "leaderboard": [
                {
                    "id": 10,
                    "bib": 22,
                    "name": "GPS Runner",
                    "last_split_index": 1,
                    "last_split_time": 1000,
                    "estimated_finish_seconds": 3000,
                }
            ],
            "errors": [],
            "upstream_stale": False,
        }
        view = server.build_event_view(bundle, now, include_course=True)
        self.assertEqual(len(view["positions"]), 1)
        position = view["positions"][0]
        self.assertEqual(position["source"], "GPS")
        self.assertEqual(position["freshness"], "LIVE")
        self.assertAlmostEqual(position["lat"], 45.02)
        self.assertAlmostEqual(position["lng"], -111.02)
        self.assertIn("course", view)

    def test_public_snapshot_removes_unused_personal_and_device_fields(self):
        payload = {
            "events": [{
                "course": {"track_points": []},
                "runners": [{"name": "Runner", "bib": 22, "city": "Town", "age": 30, "gender": "X"}],
                "positions": [{"name": "Runner", "lat": 45.0, "lng": -111.0, "battery_pct": 50, "speed_mps": 2, "heading": 180}],
            }]
        }
        public = build_static.sanitize_public(payload)
        self.assertEqual(public["delivery"], "periodic_snapshot")
        self.assertNotIn("city", public["events"][0]["runners"][0])
        self.assertNotIn("battery_pct", public["events"][0]["positions"][0])
        self.assertIn("lat", public["events"][0]["positions"][0])
        live = build_static.make_live_payload(public)
        self.assertNotIn("course", live["events"][0])
        self.assertIn("course", public["events"][0])


if __name__ == "__main__":
    unittest.main()
