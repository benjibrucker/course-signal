"""Stdlib regression tests for the terrain-aware checkpoint pilot."""
import math
import unittest
from dataclasses import FrozenInstanceError

from terrain_model import build_profile, predict


def flat(length=1000):
    distances = list(range(0, length + 1, 50))
    return build_profile(distances, [0] * len(distances))


def shaped(up_first=True):
    x = list(range(0, 2001, 50))
    z = [0 if d <= 500 else (min(d - 500, 500) * .3 if up_first
         else max(d - 1000, 0) * .3) for d in x]
    if not up_first:
        z = [min(v, 150) for v in z]
    return build_profile(x, z)


class TerrainTests(unittest.TestCase):
    def test_flat_normalization(self):
        p = flat()
        self.assertAlmostEqual(p.cumulative_effort_m[-1], 1000)
        self.assertEqual((p.gain_m, p.loss_m), (0, 0))
        r = predict(p, [0, .5, 1], [(0, 0), (1, 500)], 1, 500, 700)
        self.assertAlmostEqual(r.progress, .7)
        self.assertAlmostEqual(r.finish_seconds, 1000)

    def test_small_route_and_endpoints(self):
        p = build_profile([0, 40, 100], [10, 20, 30])
        self.assertIsNotNone(p)
        self.assertEqual((p.cumulative_m[0], p.cumulative_m[-1]), (0, 100))
        self.assertEqual((p.elevation_m[0], p.elevation_m[-1]), (10, 30))
        self.assertEqual(p.sampled_point_count, len(p.cumulative_m))

    def test_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            flat().gain_m = 9

    def test_duplicate_consistent(self):
        self.assertIsNotNone(build_profile([0, 50, 50, 100], [1, 2, 2, 3]))
        self.assertIsNone(build_profile([0, 50, 50, 100], [1, 2, 9, 3]))

    def test_bad_profiles(self):
        cases = [([], []), ([0, 100], [0]), ([0, 100], [0, None]),
                 ([0, 100], [0, math.nan]), ([0, 100], [0, math.inf]),
                 ([0, 100], [0, 9001]), ([0, 100], [-501, 0]),
                 ([0, 50, 40, 100], [0]*4), ([0, 1000], [0, 0]),
                 ([1, 101], [0, 0]), ([0, 99], [0, 0]),
                 ([0, math.inf], [0, 0]), ([0, 1e308], [0, 0])]
        for x, z in cases:
            with self.subTest(x=x, z=z):
                self.assertIsNone(build_profile(x, z))

    def test_distance_not_point_index(self):
        x = [0, 100, 200, 300, 400]
        a = build_profile(x, [0, 20, 40, 20, 0])
        dense = sorted(set(x + list(range(1, 100))))
        b = build_profile(dense, [d*.2 if d <= 200 else (400-d)*.2 for d in dense])
        for v, w in zip(a.cumulative_effort_m, b.cumulative_effort_m):
            self.assertAlmostEqual(v, w, places=8)

    def test_conservative_downhill_and_bounded_cost(self):
        p = build_profile([0, 100, 200], [100, 50, 0])
        self.assertGreaterEqual(p.cumulative_effort_m[-1], 200)
        q = build_profile([0, 50, 100], [-500, 9000, -500])
        self.assertTrue(math.isfinite(q.cumulative_effort_m[-1]))
        self.assertGreater(q.gain_m, 0)
        self.assertGreater(q.loss_m, 0)

    def test_hills_slow_within_interval(self):
        a = predict(shaped(), [0, .25, 1], [(0, 0), (1, 500)], 1, 500, 800)
        b = predict(flat(2000), [0, .25, 1], [(0, 0), (1, 500)], 1, 500, 800)
        self.assertLess(a.progress, b.progress)
        self.assertGreater(a.finish_seconds, b.finish_seconds)

    def test_same_gain_different_placement(self):
        a, b = shaped(), shaped(False)
        self.assertAlmostEqual(a.gain_m, b.gain_m)
        args = ([0, .25, 1], [(0, 0), (1, 500)], 1, 500, 800)
        self.assertLess(predict(a, *args).progress, predict(b, *args).progress)

    def test_single_interval(self):
        r = predict(flat(), [0, .25, .5, 1], [(0, 0), (2, 500)], 2, 500, 600)
        self.assertEqual(r.pace_basis, 'CHECKPOINT_AVERAGE')
        self.assertEqual(r.pace_segments_used, 1)
        self.assertEqual(r.pace_window_seconds, 500)

    def test_start_only(self):
        self.assertIsNone(predict(flat(), [0, .5, 1], [(0, 0)], 0, 0, 100))

    def test_recent_trend(self):
        r = predict(flat(), [0, .25, .5, .75, 1],
                    [(0, 0), (1, 500), (2, 750), (3, 875)], 3, 875, 900)
        self.assertEqual(r.pace_basis, 'RECENT_SEGMENTS')
        self.assertEqual(r.pace_segments_used, 3)
        self.assertLess(r.finish_seconds, 875 / .75)

    def test_terrain_normalized_recent_trend(self):
        p = shaped()
        progresses = [0, .25, .5, .75, 1]
        history = [(i, p.cumulative_effort_m[int(v*40)]) for i, v in enumerate(progresses[:4])]
        last = history[-1][1]
        r = predict(p, progresses, history, 3, last, last + 1)
        self.assertAlmostEqual(r.finish_seconds, p.cumulative_effort_m[-1])

    def test_max_three_observed_intervals(self):
        r = predict(flat(), [0, .2, .4, .6, .8, 1],
                    [(0, 0), (1, 200), (2, 400), (3, 600), (4, 800)], 4, 800, 850)
        self.assertEqual(r.pace_segments_used, 3)
        self.assertEqual(r.pace_window_seconds, 600)

    def test_keep_slow_stop_split(self):
        r = predict(flat(), [0, .25, .5, .75, 1],
                    [(0, 0), (1, 100), (2, 200), (3, 20000)], 3, 20000, 20001)
        self.assertEqual(r.pace_segments_used, 3)
        self.assertGreater(r.finish_seconds, 21000)

    def test_pilot_pace_clamp(self):
        r = predict(flat(), [0, .2, .4, .6, .8, 1],
                    [(0, 0), (1, 10000), (2, 10001), (3, 10002), (4, 10003)], 4, 10003, 10004)
        cumulative = 10003 / 800
        self.assertAlmostEqual(r.finish_seconds, 10003 + cumulative*.65*200)

    def test_next_checkpoint_hold_and_overdue(self):
        args = (flat(), [0, .25, .5, 1], [(0, 0), (1, 250)], 1, 250)
        before = predict(*args, 499)
        at = predict(*args, 500)
        after = predict(*args, 100000)
        self.assertFalse(before.overdue)
        self.assertTrue(at.overdue)
        self.assertAlmostEqual(at.next_checkpoint_seconds, 500)
        self.assertAlmostEqual(after.progress, .25 + .99*.25)
        self.assertLess(after.progress, .5)
        self.assertEqual(at.finish_seconds, after.finish_seconds)

    def test_never_crosses_checkpoint(self):
        for clock in [250, 251, 400, 500, 1000, 1e12]:
            r = predict(flat(), [0, .25, .5, 1], [(0, 0), (1, 250)], 1, 250, clock)
            self.assertGreaterEqual(r.progress, .25)
            self.assertLess(r.progress, .5)

    def test_wave_independent_chip_clocks(self):
        args = (flat(), [0, .5, 1], [(0, 0), (1, 500)], 1, 500)
        gun_epoch = 1700000000
        early_start, late_start = gun_epoch, gun_epoch + 3600
        early_now, late_now = early_start + 700, late_start + 700
        self.assertEqual(predict(*args, early_now - early_start),
                         predict(*args, late_now - late_start))
        self.assertEqual(predict(*args, 700).finish_seconds, 1000)

    def test_sub_ulp_hold_cannot_cross(self):
        upper = math.nextafter(.5, 1)
        r = predict(flat(), [0, .5, upper, 1], [(0, 0), (1, 500)], 1, 500, 1000)
        if r is not None:  # A numerically unresolvable arrival may fail closed.
            self.assertLess(r.progress, upper)

    def test_non_grid_finish_preserved(self):
        p = build_profile([0, 50, 105], [0, 10, 20])
        self.assertEqual(p.cumulative_m[-1], 105)
        self.assertEqual(p.elevation_m[-1], 20)
        self.assertEqual(p.sampled_point_count, 4)

    def test_extreme_pace_fails_closed(self):
        self.assertIsNone(predict(flat(), [0, 1e-310, 1],
                                  [(0, 0), (1, 500)], 1, 500, 600))

    def test_bad_history(self):
        bad = [[], [(1, 500)], [(0, 1), (1, 500)], [(0, 0), (1, 0)],
               [(0, 0), (1, 500), (1, 550)], [(0, 0), (2, 400), (1, 500)],
               [(0, 0), (1, 501)], [(0, 0), (1, math.inf)],
               [(0, 0), (1, math.nan)], [(0, 0), (1, -10)],
               [(0, 0), (1.0, 500)], [(0, 0), (5, 500)], [(0, 0), (1, 1e308)]]
        for history in bad:
            with self.subTest(history=history):
                self.assertIsNone(predict(flat(), [0, .5, 1], history, 1, 500, 600))

    def test_future_timestamp_and_last_mismatch(self):
        args = (flat(), [0, .5, 1], [(0, 0), (1, 500)])
        self.assertIsNone(predict(*args, 1, 500, 499))
        self.assertIsNone(predict(*args, 0, 500, 600))
        self.assertIsNone(predict(*args, 1, 501, 600))
        self.assertIsNone(predict(*args, 1, 500, math.nan))

    def test_bad_checkpoint_progresses(self):
        for v in [[0, .5, .4, 1], [0, .5, .5, 1], [0, math.nan, 1],
                  [.1, .5, 1], [0, .5, .9], [0, -.1, 1], [0, .5, 1.1]]:
            with self.subTest(progresses=v):
                self.assertIsNone(predict(flat(), v, [(0, 0), (1, 500)], 1, 500, 600))

    def test_finish_endpoint_not_projected(self):
        self.assertIsNone(predict(flat(), [0, .5, 1], [(0, 0), (2, 1000)], 2, 1000, 1100))

    def test_skipped_checkpoints_are_one_interval(self):
        r = predict(flat(), [0, .1, .2, .3, .4, .5, 1],
                    [(0, 0), (2, 200), (5, 500)], 5, 500, 600)
        self.assertEqual(r.pace_segments_used, 2)
        self.assertEqual(r.pace_window_seconds, 500)


if __name__ == '__main__':
    unittest.main()
