"""Focused synthetic contracts for completed-result analysis."""
from __future__ import annotations

import copy
import inspect
import json
import math
import unittest
from unittest.mock import patch

import result_analysis as result_analysis_module
from result_analysis import analyze_runner, search_public_runners


EVENT = {
    "event": {
        "id": "synthetic-trail-2026",
        "split_names": ["Start", "Aid One", "Aid Two", "Finish"],
    }
}
PROGRESSES = [0.0, 0.25, 0.6, 1.0]


def course(elevation=lambda _i: 100.0, *, event_id="synthetic-trail-2026", step=.001):
    return {
        "eventId": event_id,
        "trackPoints": [
            {"lat": 0.0, "lng": i * step, "ele": elevation(i)}
            for i in range(11)
        ],
    }


def result(
    runner_id,
    name,
    bib,
    times,
    *,
    offset=0.0,
    places=(None, None, None, None),
    segment_places=(None, None, None, None),
    status="FINISHED",
    **extra,
):
    latest_index = max(times)
    latest_time = times[latest_index]
    splits = []
    for index, elapsed in times.items():
        split = {"split_index": index, "elapsed_seconds": offset if index == 0 else elapsed}
        if places[index] is not None:
            split["cumulative_place"] = places[index]
        if segment_places[index] is not None:
            split["segment_place"] = segment_places[index]
        splits.append(split)
    return {
        "id": runner_id,
        "event_id": "synthetic-trail-2026",
        "name": name,
        "bib": bib,
        "status": status,
        "chip_start_seconds": offset,
        "last_split_index": latest_index,
        "last_split_time": latest_time,
        "finish_time_seconds": latest_time if status == "FINISHED" and latest_index == 3 else None,
        "finish_place": places[3] if latest_index == 3 else None,
        "splits": splits,
        **extra,
    }


def field():
    return [
        result(1, "Ada Runner", "42", {0: 0, 1: 400, 2: 1000, 3: 1800}, offset=12.5,
               places=(None, 3, 2, 3), segment_places=(None, 4, 2, 5),
               email="ada@private.test", phone="555-private", dob="1990-01-01",
               age=36, gender="X", age_group="X 30-39", chip_age_group_place=2,
               chip_gender_place=3, city="Private City", custom_fields={"secret": True},
               rsu_question_responses=["PRIVATE"]),
        result(2, "Bob Runner", "7", {0: 0, 1: 300, 2: 900, 3: 1700},
               places=(None, 1, 1, 1), age=34, gender="X", age_group="X 30-39"),
        result(3, "Cara Runner", "108", {0: 0, 1: 500, 2: 1000, 3: 1900},
               places=(None, 4, 3, 2), age=36, gender="F", age_group="F 30-39"),
        result(4, "Dee Runner", "142", {0: 0, 1: 400, 2: 1100, 3: 2000},
               places=(None, 2, 4, 4), age=44, gender="M", age_group="M 40-49"),
    ]


class PublicSearchTests(unittest.TestCase):
    def test_name_and_bib_search_are_case_insensitive_exact_prefix_then_stable_substring(self):
        rows = field()
        rows[1]["name"] = "ADA"
        rows[2]["name"] = "Ada Lovelace"
        rows[3]["name"] = "Zed Ada"
        self.assertEqual([row["id"] for row in search_public_runners(rows, "aDa", 10)], [2, 1, 3, 4])
        self.assertEqual([row["id"] for row in search_public_runners(rows, "42", 10)], [1, 4])

    def test_name_search_folds_diacritics_without_altering_public_spelling(self):
        rows = field()
        rows[0]["name"] = "José Núñez"
        matches = search_public_runners(rows, "jose nunez", 10)
        self.assertEqual([row["id"] for row in matches], [1])
        self.assertEqual(matches[0]["name"], "José Núñez")

    def test_search_is_bounded_and_validates_query_and_limit(self):
        rows = field()
        self.assertEqual(len(search_public_runners(rows, "runner", 2)), 2)
        for query in (None, 1, "", "   ", "x" * 101):
            with self.subTest(query=query), self.assertRaises((TypeError, ValueError)):
                search_public_runners(rows, query, 2)
        for limit in (True, 0, -1, 51, 1.5, "2"):
            with self.subTest(limit=limit), self.assertRaises((TypeError, ValueError)):
                search_public_runners(rows, "runner", limit)

    def test_search_returns_only_public_selection_fields(self):
        row = search_public_runners(field(), "Ada", 10)[0]
        self.assertEqual(set(row), {
            "id", "name", "bib", "status", "finish_seconds", "finish_place",
            "age", "gender", "age_group", "age_group_place", "gender_place",
        })
        self.assertEqual(
            (row["age"], row["gender"], row["age_group"], row["age_group_place"], row["gender_place"]),
            (36, "X", "X 30-39", 2, 3),
        )
        text = json.dumps(row)
        for secret in ("ada@private.test", "555-private", "1990-01-01", "Private City", "PRIVATE"):
            self.assertNotIn(secret, text)

    def test_anonymity_is_union_across_matching_sources_and_hidden_values_are_not_searchable(self):
        payload = {
            "status": "ok",
            "results": field(),
            "leaderboard": [{"id": 1, "name": "Other Secret", "bib": "999", "athlete_anonymous": True}],
        }
        self.assertEqual(search_public_runners(payload, "Ada Runner", 10), [])
        self.assertNotIn(1, [row["id"] for row in search_public_runners(payload, "42", 10)])
        public = search_public_runners(payload, "anonymous", 10)
        self.assertEqual(public[0]["name"], "Anonymous Participant")
        self.assertIsNone(public[0]["bib"])
        analyzed = analyze_runner(EVENT, course(), PROGRESSES, payload, 1)
        self.assertEqual(analyzed["runner"]["name"], "Anonymous Participant")
        self.assertIsNone(analyzed["runner"]["bib"])
        self.assertIsNone(analyzed["runner"]["age"])
        self.assertIsNone(analyzed["runner"]["gender"])
        self.assertIsNone(analyzed["runner"]["age_group"])
        self.assertNotIn("Ada Runner", json.dumps(analyzed))
        self.assertNotIn("Other Secret", json.dumps(analyzed))

    def test_anonymity_precomputation_keeps_large_search_evidence_scans_linear(self):
        count = 500
        rows = [
            {"id": runner_id, "name": f"Runner {runner_id}", "bib": str(runner_id),
             "status": "FINISHED", "is_anonymous": False}
            for runner_id in range(count)
        ]
        original = result_analysis_module._record_identifier
        with patch.object(result_analysis_module, "_record_identifier", side_effect=original) as identify:
            matches = search_public_runners(rows, "runner", 5)
        self.assertEqual([row["id"] for row in matches], list(range(5)))
        self.assertLessEqual(identify.call_count, 3 * count)

    def test_raw_result_cardinality_is_bounded_without_truncating_the_cohort(self):
        rows = [{"id": runner_id, "name": "Runner"} for runner_id in range(4)]
        with patch.object(result_analysis_module, "MAX_RESULT_ROWS", 3):
            with self.assertRaises(ValueError):
                search_public_runners(rows, "runner", 2)

    def test_has_more_metadata_accepts_only_absent_or_real_false(self):
        rows = field()
        self.assertTrue(search_public_runners({"results": rows}, "runner", 1))
        self.assertTrue(search_public_runners({"results": rows, "has_more": False}, "runner", 1))
        for malformed in (True, 1, 0, "false", "true", None, [], {}):
            with self.subTest(has_more=malformed), self.assertRaises(ValueError):
                search_public_runners({"results": rows, "has_more": malformed}, "runner", 1)

    def test_status_and_anonymity_flags_require_real_booleans(self):
        row = field()[0]
        row.update(dns="false", dq="false", dnq="false", dnf="false", dropped="false",
                   is_anonymous="false", athlete_anonymous="false")
        public = search_public_runners([row], "Ada", 1)[0]
        self.assertEqual(public["name"], "Ada Runner")
        self.assertEqual(public["status"], "FINISHED")
        analysis = analyze_runner(EVENT, course(), PROGRESSES, [row], 1)
        self.assertEqual(analysis["runner"]["status"], "FINISHED")

    def test_oversized_integer_and_numeric_string_runner_ids_are_rejected(self):
        oversized = result_analysis_module.MAX_RUNNER_ID + 1
        rows = [
            {"id": oversized, "name": "Oversized Integer"},
            {"id": str(oversized), "name": "Oversized String"},
            {"id": "9" * 10_000, "name": "Oversized Digits"},
            {"id": 1, "runner_id": "9" * 10_000, "name": "Oversized Alias"},
        ]
        self.assertEqual(search_public_runners(rows, "oversized", 10), [])
        for runner_id in (oversized, str(oversized), "9" * 10_000):
            with self.subTest(runner_id=runner_id), self.assertRaises(ValueError):
                analyze_runner(EVENT, course(), PROGRESSES, field(), runner_id)

    def test_interfaces_are_pure_and_analysis_contains_no_private_keys(self):
        event = copy.deepcopy(EVENT)
        route = course()
        progresses = list(PROGRESSES)
        rows = field()
        original = copy.deepcopy((event, route, progresses, rows))
        searched = search_public_runners(rows, "runner", 10)
        analyzed = analyze_runner(event, route, progresses, rows, 1)
        self.assertEqual((event, route, progresses, rows), original)
        forbidden = {
            "email", "phone", "dob", "sex", "city", "custom_fields",
            "rsu_question_responses", "weight_lb", "weight_kg", "height",
        }

        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()), set())
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value), set())
            return set()

        self.assertTrue(forbidden.isdisjoint(keys(searched)))
        self.assertTrue(forbidden.isdisjoint(keys(analyzed)))


class SplitAndFieldAnalysisTests(unittest.TestCase):
    def test_full_finisher_has_one_recorded_row_per_adjacent_official_section(self):
        analysis = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        self.assertEqual(analysis["analysis_status"], "complete")
        self.assertEqual(analysis["runner"], {
            "id": 1,
            "name": "Ada Runner",
            "bib": "42",
            "age": 36,
            "gender": "X",
            "age_group": "X 30-39",
            "age_group_place": 2,
            "gender_place": 3,
            "status": "FINISHED",
            "finish_seconds": 1800.0,
            "finish_place": 3,
            "last_recorded_split_index": 3,
            "last_recorded_split_name": "Finish",
        })
        self.assertEqual(len(analysis["sections"]), 3)
        self.assertEqual([row["status"] for row in analysis["sections"]], ["recorded"] * 3)
        self.assertEqual([row["segment_seconds"] for row in analysis["sections"]], [400.0, 600.0, 800.0])
        self.assertEqual([row["cumulative_seconds"] for row in analysis["sections"]], [400.0, 1000.0, 1800.0])
        self.assertEqual([(r["start_name"], r["end_name"]) for r in analysis["sections"]],
                         [("Start", "Aid One"), ("Aid One", "Aid Two"), ("Aid Two", "Finish")])
        self.assertNotIn("PRIVATE", json.dumps(analysis))
        self.assertNotIn("ada@private.test", json.dumps(analysis))

    def test_official_places_rank_change_field_aggregates_and_ties_are_correct(self):
        analysis = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        first, middle, last = analysis["sections"]
        self.assertEqual((middle["cumulative_place"], middle["segment_place"], middle["rank_change"]), (2, 2, 1))
        self.assertEqual((last["cumulative_place"], last["segment_place"], last["rank_change"]), (3, 5, -1))
        self.assertEqual((first["field_count"], first["field_median_seconds"], first["field_percentile"]),
                         (4, 400.0, 50.0))
        self.assertEqual((middle["field_count"], middle["field_median_seconds"], middle["field_percentile"]),
                         (4, 600.0, 50.0))
        self.assertEqual((last["field_count"], last["field_median_seconds"], last["field_percentile"]),
                         (4, 850.0, 75.0))
        self.assertIn("midrank", analysis["field_percentile_definition"].lower())

    def test_same_age_and_gender_group_is_compared_alongside_the_overall_field(self):
        analysis = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        first, middle, last = analysis["sections"]
        self.assertEqual(
            (first["age_group_count"], first["age_group_median_seconds"], first["age_group_percentile"]),
            (2, 350.0, 25.0),
        )
        self.assertEqual(
            (middle["age_group_count"], middle["age_group_median_seconds"], middle["age_group_percentile"]),
            (2, 600.0, 50.0),
        )
        self.assertEqual(
            (last["age_group_count"], last["age_group_median_seconds"], last["age_group_percentile"]),
            (2, 800.0, 50.0),
        )
        self.assertTrue(analysis["capabilities"]["age_group_comparison"])

    def test_sparse_history_keeps_only_adjacent_observations_recorded(self):
        rows = field()
        rows[0]["splits"] = [rows[0]["splits"][i] for i in (0, 2, 3)]
        analysis = analyze_runner(EVENT, course(), PROGRESSES, rows, 1)
        self.assertEqual(analysis["analysis_status"], "partial")
        self.assertEqual([row["status"] for row in analysis["sections"]], ["unknown", "unknown", "recorded"])
        self.assertIsNone(analysis["sections"][0]["segment_seconds"])
        self.assertEqual(analysis["sections"][2]["segment_seconds"], 800.0)
        self.assertEqual(analysis["totals"]["recorded_section_count"], 1)
        self.assertEqual(analysis["summary"]["comparison_cohort"], "overall_field")
        self.assertEqual(analysis["summary"]["valid_section_count"], 1)

    def test_dnf_stops_at_latest_recorded_checkpoint_and_never_fabricates_finish(self):
        rows = field()
        rows[0] = result(1, "Ada Runner", "42", {0: 0, 1: 400, 2: 1000}, offset=12.5,
                         places=(None, 3, 2, None), status="DNF")
        analysis = analyze_runner(EVENT, course(), PROGRESSES, rows, 1)
        self.assertEqual(analysis["runner"]["status"], "DNF")
        self.assertIsNone(analysis["runner"]["finish_seconds"])
        self.assertEqual(analysis["runner"]["last_recorded_split_name"], "Aid Two")
        self.assertEqual([row["status"] for row in analysis["sections"]], ["recorded", "recorded", "unknown"])
        finish = analysis["sections"][-1]
        self.assertEqual(finish["end_name"], "Finish")
        self.assertIsNone(finish["cumulative_seconds"])
        self.assertIsNone(finish["active_energy_kcal_per_kg"])

    def test_invalid_start_offset_does_not_create_chip_origin_but_later_adjacency_survives(self):
        for change in ("missing", "mismatch"):
            rows = field()
            if change == "missing":
                rows[0].pop("chip_start_seconds")
            else:
                rows[0]["splits"][0]["elapsed_seconds"] = 99
            analysis = analyze_runner(EVENT, course(), PROGRESSES, rows, 1)
            self.assertEqual(analysis["sections"][0]["status"], "unknown")
            self.assertEqual(analysis["sections"][1]["status"], "recorded")
            self.assertEqual(analysis["runner"]["last_recorded_split_name"], "Finish")

    def test_malformed_future_nonmonotone_nonnumeric_or_mismatched_target_fails_closed(self):
        base = field()[0]
        cases = []
        bad = copy.deepcopy(base); bad["splits"][1]["elapsed_seconds"] = "400"; cases.append(bad)
        bad = copy.deepcopy(base); bad["splits"][1]["elapsed_seconds"] = True; cases.append(bad)
        bad = copy.deepcopy(base); bad["splits"][1]["elapsed_seconds"] = float("nan"); cases.append(bad)
        bad = copy.deepcopy(base); bad["splits"][1]["elapsed_seconds"] = float("inf"); cases.append(bad)
        bad = copy.deepcopy(base); bad["splits"][1], bad["splits"][2] = bad["splits"][2], bad["splits"][1]; cases.append(bad)
        bad = copy.deepcopy(base); bad["splits"][2]["elapsed_seconds"] = 400; cases.append(bad)
        bad = copy.deepcopy(base); bad["splits"].append({"split_index": 4, "elapsed_seconds": 1900}); cases.append(bad)
        bad = copy.deepcopy(base); bad["last_split_time"] = 1799; cases.append(bad)
        bad = copy.deepcopy(base); bad["splits"][-1]["elapsed_seconds"] = 1e308; bad["last_split_time"] = 1e308; bad["finish_time_seconds"] = 1e308; cases.append(bad)
        bad = copy.deepcopy(base); bad["finish_time_seconds"] = 1801; cases.append(bad)
        bad = copy.deepcopy(base); bad["event_id"] = "other-event"; cases.append(bad)
        for row in cases:
            with self.subTest(row=row):
                analysis = analyze_runner(EVENT, course(), PROGRESSES, [row, *field()[1:]], 1)
                self.assertEqual(analysis["analysis_status"], "unavailable")
                self.assertTrue(all(section["status"] == "unknown" for section in analysis["sections"]))
                self.assertEqual(analysis["totals"]["recorded_section_count"], 0)

    def test_every_present_event_alias_must_be_nonnull_valid_and_match_the_target(self):
        variants = []
        bad = field()[0]; bad["eventId"] = "other-event"; variants.append(bad)
        bad = field()[0]; bad["eventId"] = None; variants.append(bad)
        bad = field()[0]; bad["event_id"] = "other-event"; variants.append(bad)
        bad = field()[0]; bad["event_id"] = None; bad["eventId"] = "synthetic-trail-2026"; variants.append(bad)
        bad = field()[0]; bad["eventId"] = 123; variants.append(bad)
        for row in variants:
            with self.subTest(event_id=row.get("event_id"), eventId=row.get("eventId")):
                analysis = analyze_runner(EVENT, course(), PROGRESSES, [row, *field()[1:]], 1)
                self.assertEqual(analysis["analysis_status"], "unavailable")
                self.assertTrue(all(section["status"] == "unknown" for section in analysis["sections"]))

    def test_invalid_event_or_finish_summary_exposes_no_raw_finish_identity_facts(self):
        wrong_event = field()[0]
        wrong_event.update(event_id="other-event", name="WRONG EVENT NAME", bib="WRONG-BIB")
        bad_summary = field()[0]
        bad_summary["finish_time_seconds"] = 1801
        for row in (wrong_event, bad_summary):
            with self.subTest(row=row):
                analysis = analyze_runner(EVENT, course(), PROGRESSES, [row, *field()[1:]], 1)
                runner = analysis["runner"]
                self.assertEqual(runner["status"], "INCOMPLETE")
                self.assertIsNone(runner["finish_seconds"])
                self.assertIsNone(runner["finish_place"])
                self.assertIsNone(runner["last_recorded_split_index"])
                self.assertIsNone(runner["last_recorded_split_name"])
                if row is wrong_event:
                    self.assertNotIn("WRONG EVENT NAME", json.dumps(analysis))
                    self.assertNotIn("WRONG-BIB", json.dumps(analysis))

    def test_invalid_peer_is_excluded_and_sparse_peer_contributes_only_comparable_sections(self):
        rows = field()
        rows[1]["splits"][1]["elapsed_seconds"] = "bad"
        rows[2]["splits"] = [rows[2]["splits"][i] for i in (0, 2, 3)]
        analysis = analyze_runner(EVENT, course(), PROGRESSES, rows, 1)
        self.assertEqual([section["field_count"] for section in analysis["sections"]], [2, 2, 3])

    def test_invalid_places_are_not_exposed_as_official_ranks(self):
        rows = field()
        rows[0]["splits"][1]["cumulative_place"] = True
        rows[0]["splits"][2]["segment_place"] = 0
        analysis = analyze_runner(EVENT, course(), PROGRESSES, rows, 1)
        self.assertIsNone(analysis["sections"][0]["cumulative_place"])
        self.assertIsNone(analysis["sections"][1]["segment_place"])
        self.assertIsNone(analysis["sections"][1]["rank_change"])


class TerrainAndEnergyTests(unittest.TestCase):
    def test_no_course_wrong_course_missing_elevation_bad_point_or_sparse_geometry_falls_back(self):
        variants = [
            None,
            course(event_id="other-event"),
            {"eventId": "synthetic-trail-2026", "trackPoints": [{"lat": 0.0, "lng": 0.0}, {"lat": 0.0, "lng": .001}]},
            {"eventId": "synthetic-trail-2026", "trackPoints": [
                {"lat": 0.0, "lng": 0.0, "ele": 0.0}, {"lat": None, "lng": .001, "ele": 0.0},
                {"lat": 0.0, "lng": .002, "ele": 0.0}]},
            {"eventId": "synthetic-trail-2026", "trackPoints": [
                {"lat": 0.0, "lng": 0.0, "ele": 0.0}, {"lat": 0.0, "lng": .01, "ele": 0.0}]},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                analysis = analyze_runner(EVENT, variant, PROGRESSES, field(), 1)
                self.assertFalse(analysis["capabilities"]["terrain"])
                self.assertFalse(analysis["capabilities"]["active_energy"])
                self.assertTrue(all(section["distance_m"] is None for section in analysis["sections"]))
                self.assertTrue(all(section["active_energy_kcal_per_kg"] is None for section in analysis["sections"]))
                self.assertIsNone(analysis["totals"]["active_energy_kcal_per_kg"])

    def test_invalid_split_progresses_disable_terrain_without_inventing_equal_spacing(self):
        for progresses in ([0, .6, .5, 1], [0, .5, .5, 1], [0, None, .6, 1], [0, .25, .6, .9]):
            with self.subTest(progresses=progresses):
                analysis = analyze_runner(EVENT, course(), progresses, field(), 1)
                self.assertFalse(analysis["capabilities"]["terrain"])
                self.assertTrue(all(row["distance_m"] is None for row in analysis["sections"]))
                self.assertEqual([row["status"] for row in analysis["sections"]], ["recorded"] * 3)

    def test_flat_uphill_and_downhill_metrics_are_positive_finite_and_bounded(self):
        flat = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        uphill = analyze_runner(EVENT, course(lambda i: i * 10.0), PROGRESSES, field(), 1)
        downhill = analyze_runner(EVENT, course(lambda i: 100.0 - i * 10.0), PROGRESSES, field(), 1)
        for analysis in (flat, uphill, downhill):
            self.assertTrue(analysis["capabilities"]["terrain"])
            for section in analysis["sections"]:
                for key in ("distance_m", "terrain_adjusted_effort_m", "pace_seconds_per_mile",
                            "active_energy_kcal_per_kg"):
                    self.assertTrue(math.isfinite(section[key]) and section[key] > 0)
                self.assertGreaterEqual(section["terrain_adjusted_effort_m"], section["distance_m"])
                self.assertLessEqual(section["terrain_adjusted_effort_m"],
                                     6 * math.hypot(section["distance_m"], section["gain_m"] - section["loss_m"]) + 1e-6)
        self.assertAlmostEqual(flat["sections"][0]["net_grade"], 0.0)
        self.assertGreater(uphill["sections"][0]["net_grade"], 0)
        self.assertLess(downhill["sections"][0]["net_grade"], 0)
        self.assertGreater(uphill["totals"]["terrain_adjusted_effort_m"],
                           downhill["totals"]["terrain_adjusted_effort_m"])

    def test_flat_course_effort_equals_recorded_distance(self):
        analysis = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        for section in analysis["sections"]:
            self.assertAlmostEqual(section["terrain_adjusted_effort_m"], section["distance_m"])
        self.assertAlmostEqual(
            analysis["totals"]["terrain_adjusted_effort_m"],
            analysis["totals"]["distance_m"],
        )

    def test_flat_energy_uses_3_6_joules_per_kg_meter_and_range_is_non_statistical(self):
        analysis = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        for section in analysis["sections"]:
            expected = section["terrain_adjusted_effort_m"] * 3.6 / 4184.0
            self.assertAlmostEqual(section["active_energy_kcal_per_kg"], expected)
            energy_range = section["active_energy_communication_range_kcal_per_kg"]
            self.assertAlmostEqual(energy_range["lower"], expected * .75)
            self.assertAlmostEqual(energy_range["upper"], expected * 1.25)
        self.assertIn("non-statistical", analysis["active_energy_range_definition"].lower())
        self.assertIn("25%", analysis["active_energy_range_definition"])

    def test_active_energy_and_effort_are_additive_across_recorded_sections(self):
        analysis = analyze_runner(EVENT, course(lambda i: i * 5.0), PROGRESSES, field(), 1)
        sections = analysis["sections"]
        self.assertAlmostEqual(analysis["totals"]["terrain_adjusted_effort_m"],
                               sum(row["terrain_adjusted_effort_m"] for row in sections))
        self.assertAlmostEqual(analysis["totals"]["active_energy_kcal_per_kg"],
                               sum(row["active_energy_kcal_per_kg"] for row in sections))
        total_range = analysis["totals"]["active_energy_communication_range_kcal_per_kg"]
        self.assertAlmostEqual(total_range["lower"], sum(row["active_energy_communication_range_kcal_per_kg"]["lower"] for row in sections))
        self.assertAlmostEqual(total_range["upper"], sum(row["active_energy_communication_range_kcal_per_kg"]["upper"] for row in sections))

    def test_per_kg_output_is_independent_of_lb_kg_and_demographic_source_fields(self):
        baseline = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        rows = field()
        rows[0].update(weight_lb=999, weight_kg=1, age=99, sex="PRIVATE", height=250)
        altered = analyze_runner(EVENT, course(), PROGRESSES, rows, 1)
        for base_row, altered_row in zip(baseline["sections"], altered["sections"]):
            self.assertEqual(base_row["active_energy_kcal_per_kg"], altered_row["active_energy_kcal_per_kg"])
            self.assertEqual(
                base_row["active_energy_communication_range_kcal_per_kg"],
                altered_row["active_energy_communication_range_kcal_per_kg"],
            )
        self.assertEqual(
            baseline["totals"]["active_energy_kcal_per_kg"],
            altered["totals"]["active_energy_kcal_per_kg"],
        )
        self.assertEqual(list(inspect.signature(analyze_runner).parameters),
                         ["event_response", "course", "split_progresses", "raw_results", "runner_id"])


class DescriptiveSummaryTests(unittest.TestCase):
    def test_summary_is_structured_descriptive_and_has_explicit_capabilities_and_limits(self):
        analysis = analyze_runner(EVENT, course(), PROGRESSES, field(), 1)
        summary = analysis["summary"]
        self.assertEqual(summary["comparison_cohort"], "overall_field")
        self.assertEqual(summary["valid_section_count"], 3)
        self.assertEqual(summary["strongest_comparable_section"]["end_name"], "Finish")
        self.assertEqual(summary["weakest_comparable_section"]["end_name"], "Aid One")
        self.assertEqual(summary["largest_places_gained"]["rank_change"], 1)
        self.assertEqual(summary["largest_places_lost"]["rank_change"], -1)
        self.assertEqual(summary["consistency"]["status"], "available")
        self.assertEqual(summary["trend"]["status"], "available")
        self.assertEqual(set(analysis["capabilities"]), {
            "splits", "field_comparison", "age_group_comparison", "terrain", "active_energy",
        })
        self.assertTrue(analysis["limitations"])
        text = json.dumps(analysis).lower()
        for prohibited in ("medical advice", "caused by", "training recommendation"):
            self.assertNotIn(prohibited, text)

    def test_missing_runner_id_is_rejected_instead_of_selecting_another_record(self):
        with self.assertRaises(LookupError):
            analyze_runner(EVENT, course(), PROGRESSES, field(), 999)


if __name__ == "__main__":
    unittest.main()
