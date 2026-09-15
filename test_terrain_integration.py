"""Synthetic end-to-end terrain pilot and optional bulk-history contracts."""

import datetime as dt
import json
import unittest
from unittest.mock import patch

import server

START = dt.datetime(2026, 9, 11, 10, tzinfo=dt.timezone.utc)
OFFSET = 10.745
NOW = START + dt.timedelta(seconds=OFFSET + 1900)


def fixture():
    runner = dict(id=1, name="Synthetic Runner", bib=9, chip_start_seconds=OFFSET,
                  last_split_index=3, last_split_time=1800)
    history = {**runner, "splits": [{"split_index": i, "elapsed_seconds": t, "email": "PRIVATE"}
                                      for i, t in enumerate([OFFSET, 400, 1000, 1800])]}
    return {
        "event_response": {"event": {"id": "the-rut-21k-2026", "event_date": "2026-09-11",
            "start_time": "10:00:00", "timezone": "UTC", "course_status": "active",
            "split_names": ["Start", "A", "B", "C", "D", "Finish"]},
            "multipliers": [{"split_index": i, "progress_pct": i / 5, "pace_multiplier": 999}
                            for i in range(6)]},
        "course_response": {"courseMaps": [{"trackPoints": [
            {"lat": 0, "lng": i * .001, "ele": 1000 + i * 5} for i in range(21)]}]},
        "leaderboard": [runner], "gps_response": {"positions": []}, "history": [history],
        "history_status": "fresh", "history_fetched_at": server.timestamp(NOW),
        "errors": [], "upstream_stale": False,
    }


def view(data=None, now=NOW):
    return server.build_event_view(data if data is not None else fixture(), now, True)


class TerrainIntegrationTests(unittest.TestCase):
    def test_chip_split_zero_and_rolling_metadata(self):
        data = fixture()
        times, status = server.checkpoint_history(data["history"][0], data["leaderboard"][0], 1900)
        self.assertEqual(times, [(0, 0), (1, 400), (2, 1000), (3, 1800)])
        self.assertEqual(status, "matched")
        result = view(data); row = result["positions"][0]
        self.assertTrue(result["estimator"]["terrain_ready"])
        self.assertEqual(result["estimator"]["historical_baseline"], "not_applied")
        self.assertEqual(row["estimate_model"], "terrain-pilot-v1")
        self.assertEqual(row["estimate_basis"], "TERRAIN_CHECKPOINT_PILOT")
        self.assertEqual(row["eta_basis"], "TERRAIN_CHECKPOINT_PILOT")
        self.assertEqual(row["pace_basis"], "RECENT_SEGMENTS")
        self.assertEqual(row["pace_segments_used"], 3)
        self.assertEqual(row["pace_window_seconds"], 1800)
        self.assertTrue(row["rank_eligible"])
        self.assertIn("next_checkpoint_at", row)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("splits", row)

    def test_latest_mismatch_and_offset_mismatch_use_cumulative_terrain(self):
        for changes in ({"last_split_index": 2}, {"last_split_time": 1800.02}, {"chip_start_seconds": 12}):
            data = fixture(); data["history"][0].update(changes)
            row = view(data)["positions"][0]
            self.assertEqual(row["checkpoint_history_status"], "mismatch")
            self.assertEqual(row["pace_basis"], "CHECKPOINT_AVERAGE")
            self.assertEqual(row["estimate_basis"], "TERRAIN_CHECKPOINT_PILOT")
            self.assertEqual(row["last_split_index"], 3)

    def test_invalid_history_order_duplicate_future_and_nonfinite(self):
        for splits in ([{ "split_index": 2, "elapsed_seconds": 1000}, {"split_index": 1, "elapsed_seconds": 1200}],
                       [{"split_index": 1, "elapsed_seconds": 400}] * 2,
                       [{"split_index": 3, "elapsed_seconds": 2000}],
                       [{"split_index": 1, "elapsed_seconds": float("nan")}], [None]):
            data = fixture(); data["history"][0]["splits"] = splits
            row = view(data)["positions"][0]
            self.assertEqual(row["pace_basis"], "CHECKPOINT_AVERAGE")
            self.assertEqual(row["checkpoint_history_status"], "malformed")

    def test_missing_checkpoint_is_one_whole_interval(self):
        data = fixture(); del data["history"][0]["splits"][2]
        row = view(data)["positions"][0]
        self.assertEqual(row["pace_segments_used"], 2)
        self.assertEqual(row["pace_window_seconds"], 1800)

    def test_stale_history_is_optional_but_core_stale_holds(self):
        data = fixture(); data["history_fetched_at"] = server.timestamp(NOW - dt.timedelta(seconds=60))
        result = view(data); row = result["positions"][0]
        self.assertEqual(result["estimator"]["history_status"], "stale")
        self.assertFalse(result["upstream_stale"])
        self.assertEqual(row["pace_basis"], "CHECKPOINT_AVERAGE")
        data["upstream_stale"] = True
        row = view(data)["positions"][0]
        self.assertEqual(row["progress"], .6)
        self.assertTrue(row["estimate_held"])
        self.assertIsNone(row["eta_at"])
        self.assertNotIn("next_checkpoint_at", row)

    def test_privacy_union_even_unusable_history(self):
        data = fixture(); data["history"][0]["athlete_anonymous"] = True
        data["history_status"] = "unavailable"
        result = view(data)
        for row in (result["runners"][0], result["positions"][0]):
            self.assertEqual(row["name"], "Anonymous Participant")
            self.assertIsNone(row["bib"])
        self.assertNotIn("Synthetic Runner", json.dumps(result))

    def test_terminal_flags_from_history_or_gps(self):
        for flag in ("dropped", "dnf", "dns", "dq", "dnq", "finish_time_seconds"):
            data = fixture(); data["history"][0][flag] = 1
            self.assertEqual(view(data)["positions"], [])
        data = fixture()
        data["gps_response"]["positions"] = [dict(runner_id=1, latitude=0, longitude=.013,
            recorded_at=server.timestamp(NOW), dnf=True)]
        row = view(data)["positions"][0]
        self.assertEqual(row["source"], "GPS")
        self.assertFalse(row["rank_eligible"])
        self.assertEqual(row["status"], "DROPPED")

    def test_missing_elevation_and_disable_preserve_coarse_model(self):
        data = fixture(); del data["course_response"]["courseMaps"][0]["trackPoints"][1]["ele"]
        result = view(data)
        self.assertFalse(result["estimator"]["terrain_ready"])
        self.assertEqual(result["positions"][0]["estimate_basis"], "CHECKPOINT_PACE_CHIP")
        with patch.object(server, "TERRAIN_PILOT_ENABLED", False):
            result = view()
        self.assertFalse(result["estimator"]["terrain_ready"])
        self.assertEqual(result["positions"][0]["estimate_basis"], "CHECKPOINT_PACE_CHIP")

    def test_malformed_elevation_is_a_fallback_not_a_cache_key_error(self):
        for value in ({'meters': 1000}, [1000], float('nan'), True, 'unknown'):
            data = fixture()
            data['course_response']['courseMaps'][0]['trackPoints'][1]['ele'] = value
            result = view(data)
            self.assertFalse(result['estimator']['terrain_ready'])
            self.assertEqual(result['positions'][0]['estimate_basis'], 'CHECKPOINT_PACE_CHIP')

    def test_start_and_clock_safety(self):
        for change in ({"last_split_index": 0, "last_split_time": 0, "goal_time_seconds": 5000},
                       {"chip_start_seconds": None}, {"last_split_time": 1901}, {"chip_start_seconds": 5000}):
            data = fixture(); data["leaderboard"][0].update(change)
            self.assertEqual(view(data)["positions"], [])
        data = fixture(); data["event_response"]["event"]["start_time"] = None
        self.assertEqual(view(data)["positions"], [])

    def test_profile_is_cached_and_no_existing_multiplier_stacking(self):
        server.terrain_profile.cache_clear()
        with patch.object(server, "build_profile", wraps=server.build_profile) as build, patch.object(
                server, "segment_weights", side_effect=AssertionError("legacy weights called")):
            first = view(); second = view()
            self.assertEqual(build.call_count, 1)
        data = fixture()
        for row in data["event_response"]["multipliers"]:
            row["pace_multiplier"] = .01
        self.assertEqual(first["positions"][0]["eta_at"], view(data)["positions"][0]["eta_at"])

    def test_predict_none_falls_back_but_invalid_clock_never_does(self):
        with patch.object(server, "predict", return_value=None):
            self.assertEqual(view()["positions"][0]["estimate_basis"], "CHECKPOINT_PACE_CHIP")
            data = fixture(); data["leaderboard"][0]["last_split_time"] = 1901
            self.assertEqual(view(data)["positions"], [])

    def test_duplicate_gps_terminal_and_history_privacy_survive(self):
        data = fixture()
        data["gps_response"]["positions"] = [
            dict(runner_id=1, latitude=0, longitude=.013, recorded_at=server.timestamp(NOW - dt.timedelta(seconds=1)),
                 status="REGISTERED", dnf=True),
            dict(runner_id=1, latitude=0, longitude=.014, recorded_at=server.timestamp(NOW))]
        data["history"].append({**data["history"][0], "is_anonymous": True})
        row = view(data)["positions"][0]
        self.assertEqual(row["status"], "DROPPED")
        self.assertEqual(row["name"], "Anonymous Participant")
        self.assertEqual(row["lng"], .014)
        self.assertFalse(row["rank_eligible"])

    def test_bulk_validation_ids_totals_pagination(self):
        valid = {"status": "ok", "results": [{"id": 1}], "total": 1, "total_count": 1}
        self.assertIsNone(server.validate_history_payload(valid))
        for changes in ({"has_more": True}, {"total": 2}, {"total_count": 0}, {"total": True},
                        {"results": [{"id": 1}, {"id": 1}]}, {"results": [{"id": None}]},
                        {"results": {}}, {"status": "error"}):
            self.assertIsNotNone(server.validate_history_payload({**valid, **changes}))

    def test_cross_event_history_latency_cannot_expire_other_event_gps(self):
        import threading
        ids = ['the-rut-21k-2026', 'the-rut-28k-2026']
        now = NOW
        clock = [now - dt.timedelta(seconds=15)]
        fetched = {server.UPSTREAM + '/events/' + ids[0]: now - dt.timedelta(seconds=40)}
        fast_core = threading.Event()
        data = fixture()
        data['gps_response']['positions'] = [dict(runner_id=1, latitude=0, longitude=.013,
            recorded_at=server.timestamp(now), accuracy=5)]
        def fetch(path, ttl):
            if path.endswith('/results'):
                if ids[1] in path:
                    fast_core.wait(.2)
                    clock[0] = now
                    raise TimeoutError('synthetic slow history in another race')
                fetched[server.UPSTREAM + path] = clock[0]
                return {'results': data['history']}, False
            key = server.UPSTREAM + path
            if key not in fetched or (clock[0] - fetched[key]).total_seconds() >= ttl:
                fetched[key] = clock[0]
            if path == '/events/' + ids[0]:
                fast_core.set()
            if 'leaderboard?' in path:
                return {'leaderboard': data['leaderboard'], 'has_more': False}, False
            if path.startswith('/course-maps/'):
                return data['course_response'], False
            if path.startswith('/gps/'):
                return data['gps_response'], False
            return {**data['event_response'], 'event': {**data['event_response']['event'], 'id': path.rsplit('/',1)[-1]}}, False
        with patch.object(server, 'EVENTS', tuple(zip(ids, ['21K', '28K']))), patch.object(
                server, 'upstream_json', side_effect=fetch), patch.object(
                server.CACHE, 'source_fetched_at', side_effect=lambda url: server.timestamp(fetched[url]) if url in fetched else None):
            payload = server.build_payload(now=now)
        result = next(e for e in payload['events'] if e['id'] == ids[0])
        self.assertFalse(result['upstream_stale'])
        self.assertEqual(result['positions'][0]['freshness'], 'LIVE')
        self.assertTrue(result['positions'][0]['rank_eligible'])

    def test_optional_latency_cannot_expire_healthy_event_metadata(self):
        data = fixture()
        clock = [NOW - dt.timedelta(seconds=15)]
        fetched = {server.UPSTREAM + '/events/the-rut-21k-2026': NOW - dt.timedelta(seconds=40)}
        data['gps_response']['positions'] = [dict(runner_id=1, latitude=0, longitude=.013,
            recorded_at=server.timestamp(NOW), accuracy=5)]
        def fetch(path, ttl):
            if path.endswith('/results'):
                clock[0] += dt.timedelta(seconds=15)
                raise TimeoutError('synthetic optional timeout')
            key = server.UPSTREAM + path
            if key not in fetched or (clock[0] - fetched[key]).total_seconds() >= ttl:
                fetched[key] = clock[0]
            if 'leaderboard?' in path:
                return {'leaderboard': data['leaderboard'], 'has_more': False}, False
            if path.startswith('/course-maps/'):
                return data['course_response'], False
            if path.startswith('/gps/'):
                return data['gps_response'], False
            return data['event_response'], False
        with patch.object(server, 'upstream_json', side_effect=fetch), patch.object(
                server.CACHE, 'source_fetched_at', side_effect=lambda url: server.timestamp(fetched[url]) if url in fetched else None):
            loaded = server.load_event_bundle('the-rut-21k-2026')
        result = view(loaded)
        self.assertFalse(result['upstream_stale'])
        self.assertEqual(result['positions'][0]['freshness'], 'LIVE')
        self.assertTrue(result['positions'][0]['rank_eligible'])
        self.assertEqual(result['estimator']['history_status'], 'unavailable')

    def test_optional_source_failure_does_not_degrade_gps(self):
        data = fixture(); calls = []
        data["gps_response"]["positions"] = [dict(runner_id=1, latitude=0, longitude=.013,
            recorded_at=server.timestamp(NOW), accuracy=5)]
        def fetch(path, ttl):
            calls.append(path)
            if path.endswith("/results"):
                self.assertEqual(ttl, 60)
                raise RuntimeError("PRIVATE diagnostic")
            if "leaderboard?" in path:
                return {"leaderboard": data["leaderboard"], "has_more": False}, False
            if path.startswith("/course-maps/"):
                return data["course_response"], False
            if path.startswith("/gps/"):
                return data["gps_response"], False
            return data["event_response"], False
        with patch.object(server, "upstream_json", side_effect=fetch), patch.object(
                server.CACHE, "source_fetched_at", return_value=server.timestamp(NOW)):
            loaded = server.load_event_bundle("the-rut-21k-2026")
        result = view(loaded); row = result["positions"][0]
        self.assertEqual(result["estimator"]["history_status"], "unavailable")
        self.assertEqual(result["errors"], [])
        self.assertNotIn("history", result["source_freshness"])
        self.assertFalse(result["upstream_stale"])
        self.assertEqual(row["source"], "GPS")
        self.assertEqual(row["freshness"], "LIVE")
        self.assertTrue(row["rank_eligible"])
        self.assertEqual(row["pace_basis"], "CHECKPOINT_AVERAGE")
        self.assertEqual(len([p for p in calls if p.endswith("/results")]), 1)
        self.assertLess(next(i for i,p in enumerate(calls) if p.endswith("/results")),
                        next(i for i,p in enumerate(calls) if p.startswith("/gps/")))
        self.assertNotIn("PRIVATE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
