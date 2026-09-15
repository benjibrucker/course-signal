"""RED-first privacy and cache contracts for :mod:`completed_results`."""
from __future__ import annotations

import copy
from http.client import HTTPMessage
from io import BytesIO
import json
import threading
import unittest
from unittest.mock import patch

import completed_results as completed_results_module
from competitive_catalog import InvalidIdentifier
from completed_results import (
    API_HOST,
    LEADERBOARD_PAGE_SIZE,
    MAX_LEADERBOARD_LOAD_SECONDS,
    MAX_RESPONSE_BYTES,
    MAX_RESULT_ROWS,
    MAX_SOURCE_TEXT_CHARS,
    MAX_SPLIT_READINGS,
    MAX_TOTAL_SPLIT_READINGS,
    CompletedResultsCache,
    CompletedResultsUnavailable,
    fetch_leaderboard_payload,
    fetch_results_payload,
    sanitize_leaderboard_payload,
    sanitize_results_payload,
)


PRIVATE_SENTINELS = {
    "PRIVATE_EMAIL_SENTINEL",
    "PRIVATE_PHONE_SENTINEL",
    "PRIVATE_ADDRESS_SENTINEL",
    "PRIVATE_DOB_SENTINEL",
    "PRIVATE_CUSTOM_SENTINEL",
    "PRIVATE_REGISTRATION_SENTINEL",
    "PRIVATE_URL_SENTINEL",
    "PRIVATE_NOTE_SENTINEL",
    "PRIVATE_METADATA_SENTINEL",
    "PRIVATE_ANONYMOUS_NAME",
    "PRIVATE_ANONYMOUS_BIB",
}
PRIVATE_KEYS = {
    "email", "phone", "address", "street", "city", "state", "zip", "postal_code",
    "dob", "date_of_birth", "sex", "custom_fields", "custom_answers",
    "registration", "registration_id", "registration_ids", "url", "urls", "notes",
    "metadata", "rsu_question_responses", "user", "video", "weight", "height",
}


def all_keys(value):
    if isinstance(value, dict):
        found = set(value)
        for nested in value.values():
            found.update(all_keys(nested))
        return found
    if isinstance(value, (list, tuple)):
        found = set()
        for nested in value:
            found.update(all_keys(nested))
        return found
    return set()


def assert_private_free(testcase, value):
    testcase.assertTrue(PRIVATE_KEYS.isdisjoint(all_keys(value)))
    rendered = repr(value)
    for sentinel in PRIVATE_SENTINELS:
        testcase.assertNotIn(sentinel, rendered)


def result_row(runner_id, *, name=None, split_count=2, anonymous=False):
    return {
        "id": runner_id,
        "event_id": "event-one",
        "name": name or f"Runner {runner_id}",
        "bib": str(runner_id),
        "status": "FINISHED",
        "is_anonymous": anonymous,
        "chip_start_seconds": 12.5,
        "last_split_index": split_count - 1,
        "last_split_time": float((split_count - 1) * 100),
        "finish_time_seconds": float((split_count - 1) * 100),
        "finish_place": runner_id + 1 if isinstance(runner_id, int) else 1,
        "splits": [
            {
                "split_index": index,
                "elapsed_seconds": 12.5 if index == 0 else float(index * 100),
                "cumulative_place": index + 1,
                "segment_place": index + 2,
            }
            for index in range(split_count)
        ],
    }


def payload_for(event_id):
    row = result_row(1, name=f"Public {event_id}")
    row["event_id"] = event_id
    row.update({
        "email": "PRIVATE_EMAIL_SENTINEL",
        "metadata": {"nested": {"notes": "PRIVATE_METADATA_SENTINEL"}},
    })
    row["splits"][0]["custom_fields"] = {
        "deep": ["PRIVATE_CUSTOM_SENTINEL", {"phone": "PRIVATE_PHONE_SENTINEL"}]
    }
    return {"status": "ok", "results": [row]}


class SanitizerTests(unittest.TestCase):
    def test_result_allowlist_strips_nested_private_data_without_mutating_input(self):
        public = result_row(7, name="Public Runner")
        public.update({
            "runner_id": 7,
            "email": "PRIVATE_EMAIL_SENTINEL",
            "phone": {"nested": "PRIVATE_PHONE_SENTINEL"},
            "address": ["PRIVATE_ADDRESS_SENTINEL"],
            "dob": "PRIVATE_DOB_SENTINEL",
            "age": 44,
            "gender": "F",
            "age_group": "F 40-49",
            "chip_age_group_place": 12,
            "chip_gender_place": 28,
            "sex": "PRIVATE_CUSTOM_SENTINEL",
            "custom_fields": {"a": [{"b": "PRIVATE_CUSTOM_SENTINEL"}]},
            "registration_id": "PRIVATE_REGISTRATION_SENTINEL",
            "url": "PRIVATE_URL_SENTINEL",
            "notes": "PRIVATE_NOTE_SENTINEL",
            "metadata": {"secret": "PRIVATE_METADATA_SENTINEL"},
        })
        public["splits"][0].update({
            "email": "PRIVATE_EMAIL_SENTINEL",
            "metadata": {"deep": "PRIVATE_METADATA_SENTINEL"},
        })
        anonymous = result_row(8, name="PRIVATE_ANONYMOUS_NAME", anonymous=True)
        anonymous["bib"] = "PRIVATE_ANONYMOUS_BIB"
        nested_bad = result_row(9)
        nested_bad["finish_time_seconds"] = {"value": "PRIVATE_METADATA_SENTINEL"}
        nested_bad["name"] = "PRIVATE_ANONYMOUS_NAME"
        nested_bad["bib"] = "PRIVATE_ANONYMOUS_BIB"
        nested_bad["is_anonymous"] = {"secret": "PRIVATE_CUSTOM_SENTINEL"}
        raw = {"status": "ok", "total": 3, "results": [public, anonymous, nested_bad]}
        original = copy.deepcopy(raw)

        clean = sanitize_results_payload(raw, event_id="event-one")

        self.assertEqual(raw, original)
        self.assertEqual([row["id"] for row in clean], [7, 8, 9])
        self.assertEqual(clean[0]["name"], "Public Runner")
        self.assertEqual(clean[0]["bib"], "7")
        self.assertEqual(
            {key: clean[0][key] for key in (
                "age", "gender", "age_group", "chip_age_group_place", "chip_gender_place",
            )},
            {
                "age": 44, "gender": "F", "age_group": "F 40-49",
                "chip_age_group_place": 12, "chip_gender_place": 28,
            },
        )
        self.assertNotIn("name", clean[1])
        self.assertNotIn("bib", clean[1])
        self.assertEqual(clean[2]["splits"], [])
        self.assertTrue(clean[2]["is_anonymous"])
        self.assertNotIn("name", clean[2])
        self.assertNotIn("bib", clean[2])
        self.assertLessEqual(len(clean[0]["name"]), 200)
        self.assertLessEqual(len(clean[0]["bib"]), 64)
        assert_private_free(self, clean)

    def test_leaderboard_is_identity_anonymity_evidence_only_and_unions_duplicates(self):
        raw = {
            "status": "ok",
            "leaderboard": [
                {"id": "7", "name": "PRIVATE_ANONYMOUS_NAME", "bib": "PRIVATE_ANONYMOUS_BIB",
                 "is_anonymous": False, "email": "PRIVATE_EMAIL_SENTINEL"},
                {"id": "7", "athlete_anonymous": True,
                 "metadata": {"secret": "PRIVATE_METADATA_SENTINEL"}},
                {"runner_id": 8, "is_anonymous": True, "phone": "PRIVATE_PHONE_SENTINEL"},
            ],
        }
        original = copy.deepcopy(raw)

        clean = sanitize_leaderboard_payload(raw)

        self.assertEqual(raw, original)
        self.assertEqual(clean, [
            {"id": "7", "is_anonymous": False, "athlete_anonymous": True},
            {"runner_id": 8, "is_anonymous": True},
        ])
        assert_private_free(self, clean)

    def test_anonymity_from_later_duplicate_scrubs_first_result_identity(self):
        first = result_row(1, name="PRIVATE_ANONYMOUS_NAME")
        first["bib"] = "PRIVATE_ANONYMOUS_BIB"
        first.update(age=39, gender="F", age_group="F 30-39", chip_age_group_place=3)
        later = {"id": 1, "event_id": "event-one", "athlete_anonymous": True}
        clean = sanitize_results_payload({"results": [first, later]}, event_id="event-one")
        self.assertEqual(len(clean), 1)
        self.assertTrue(clean[0]["athlete_anonymous"])
        self.assertNotIn("name", clean[0])
        self.assertNotIn("bib", clean[0])
        for field in ("age", "gender", "age_group", "chip_age_group_place"):
            self.assertNotIn(field, clean[0])
        assert_private_free(self, clean)

    def test_public_demographics_are_strictly_bounded_and_malformed_values_are_dropped(self):
        valid = result_row(1)
        valid.update(
            age=39, gender="F", age_group="F 30-39",
            chip_age_group_place="2", gun_age_group_place=3,
            chip_gender_place="20", gun_gender_place=21,
        )
        malformed = result_row(2)
        malformed.update(
            age=True, gender={"private": True}, age_group="x" * 1025,
            chip_age_group_place=0, gun_age_group_place=True,
            chip_gender_place=-1, gun_gender_place="02",
        )
        clean = sanitize_results_payload({"results": [valid, malformed]}, event_id="event-one")
        self.assertEqual(
            {key: clean[0][key] for key in (
                "age", "gender", "age_group", "chip_age_group_place", "gun_age_group_place",
                "chip_gender_place", "gun_gender_place",
            )},
            {
                "age": 39, "gender": "F", "age_group": "F 30-39",
                "chip_age_group_place": 2, "gun_age_group_place": 3,
                "chip_gender_place": 20, "gun_gender_place": 21,
            },
        )
        for field in (
            "age", "gender", "age_group", "chip_age_group_place", "gun_age_group_place",
            "chip_gender_place", "gun_gender_place",
        ):
            self.assertNotIn(field, clean[1])

    def test_malformed_payload_structures_and_conflicting_counts_fail_closed(self):
        malformed = (
            None,
            [],
            {},
            {"status": "error", "results": []},
            {"results": {}},
            {"results": [], "has_more": True},
            {"results": [], "total": 1},
            {"results": [], "count": True},
        )
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaises(CompletedResultsUnavailable):
                sanitize_results_payload(raw)
        for raw in (None, {}, {"status": "error", "leaderboard": []},
                    {"leaderboard": {}}, {"leaderboard": [], "total": 1}):
            with self.subTest(raw=raw), self.assertRaises(CompletedResultsUnavailable):
                sanitize_leaderboard_payload(raw)

    def test_has_more_requires_a_real_boolean_and_bulk_results_are_complete(self):
        for field, sanitizer in (("results", sanitize_results_payload),
                                 ("leaderboard", sanitize_leaderboard_payload)):
            self.assertEqual(sanitizer({field: [], "has_more": False}), [])
            with self.assertRaises(CompletedResultsUnavailable):
                sanitizer({field: [], "has_more": True})
            for malformed in (None, 0, 1, "", "false", "true"):
                with self.subTest(field=field, malformed=malformed), \
                        self.assertRaises(CompletedResultsUnavailable):
                    sanitizer({field: [], "has_more": malformed})

    def test_declared_total_over_result_limit_is_rejected_not_truncated(self):
        rows = [None] * 100_001
        with self.assertRaises(CompletedResultsUnavailable):
            sanitize_results_payload({"results": rows, "total": len(rows)})

    def test_raw_result_and_leaderboard_lengths_fail_at_boundary_plus_one(self):
        boundary = [None] * MAX_RESULT_ROWS
        self.assertEqual(sanitize_results_payload({"results": boundary}), [])
        self.assertEqual(sanitize_leaderboard_payload({"leaderboard": boundary}), [])

        oversized_results = [None] * (MAX_RESULT_ROWS + 1)
        oversized_leaderboard = [None] * (MAX_RESULT_ROWS + 1)
        with self.assertRaises(CompletedResultsUnavailable):
            sanitize_results_payload({"results": oversized_results})
        with self.assertRaises(CompletedResultsUnavailable):
            sanitize_leaderboard_payload({"leaderboard": oversized_leaderboard})

    def test_every_raw_split_list_is_bounded_before_scanning(self):
        duplicate = {"split_index": 0, "elapsed_seconds": 1}
        boundary = {"id": 1, "splits": [duplicate] * MAX_SPLIT_READINGS}
        clean = sanitize_results_payload({"results": [boundary]})
        self.assertEqual(clean[0]["splits"], [{"split_index": 0, "elapsed_seconds": 1.0}])

        for splits in (
            [duplicate] * (MAX_SPLIT_READINGS + 1),
            [None] * (MAX_SPLIT_READINGS + 1),
            [duplicate] * 100_001,
        ):
            raw = result_row(1, split_count=0)
            raw["splits"] = splits
            with self.subTest(length=len(splits)), \
                    self.assertRaises(CompletedResultsUnavailable):
                sanitize_results_payload({"results": [raw]})

    def test_source_text_and_numeric_scalars_are_bounded_before_normalization(self):
        boundary = sanitize_results_payload({"results": [{
            "id": 1,
            "name": "n" * MAX_SOURCE_TEXT_CHARS,
            "bib": "b" * MAX_SOURCE_TEXT_CHARS,
            "last_split_time": 1,
        }]})[0]
        self.assertEqual(len(boundary["name"]), 200)
        self.assertEqual(len(boundary["bib"]), 64)

        class HostileInt(int):
            def __float__(self):
                raise AssertionError("oversized numeric scalar was normalized")

        oversized = sanitize_results_payload({"results": [{
            "id": 2,
            "name": "n" * (MAX_SOURCE_TEXT_CHARS + 1),
            "bib": "b" * (MAX_SOURCE_TEXT_CHARS + 1),
            "last_split_time": HostileInt(1),
            "finish_time_seconds": 10 ** 100_000,
        }]})[0]
        self.assertNotIn("name", oversized)
        self.assertNotIn("bib", oversized)
        self.assertNotIn("last_split_time", oversized)
        self.assertNotIn("finish_time_seconds", oversized)

    def test_aggregate_split_budget_fails_closed_before_output_allocation(self):
        full_rows, remainder = divmod(MAX_TOTAL_SPLIT_READINGS, MAX_SPLIT_READINGS)
        splits = [
            {"split_index": index, "elapsed_seconds": float(index)}
            for index in range(MAX_SPLIT_READINGS)
        ]
        rows = [{"id": index, "splits": splits} for index in range(full_rows)]
        if remainder:
            rows.append({"id": len(rows), "splits": splits[:remainder]})
        clean = sanitize_results_payload({"results": rows})
        self.assertEqual(sum(len(row["splits"]) for row in clean), MAX_TOTAL_SPLIT_READINGS)

        over_budget = rows + [{"id": len(rows), "splits": [splits[0]]}]
        with self.assertRaises(CompletedResultsUnavailable):
            sanitize_results_payload({"results": over_budget})

        ten_million_shape = [{"id": 1, "splits": splits}] * MAX_RESULT_ROWS
        with self.assertRaises(CompletedResultsUnavailable):
            sanitize_results_payload({"results": ten_million_shape})

    def test_all_retained_integer_scalars_have_finite_bounds(self):
        oversized = 10 ** 100
        row = {
            "id": 1,
            "bib": oversized,
            "finish_place": oversized,
            "last_split_index": 0,
            "last_split_time": 1,
            "splits": [{
                "split_index": 0,
                "elapsed_seconds": 1,
                "cumulative_place": oversized,
            }],
        }
        clean = sanitize_results_payload({"results": [row]})[0]
        self.assertFalse("bib" in clean)
        self.assertFalse("finish_place" in clean)
        self.assertFalse("cumulative_place" in clean["splits"][0])

        numeric_text_bib = sanitize_results_payload({
            "results": [{"id": 2, "bib": "9" * 65}],
        })[0]
        self.assertFalse("bib" in numeric_text_bib)

        invalid_index = sanitize_results_payload({
            "results": [{
                "id": 3,
                "last_split_index": oversized,
                "last_split_time": 1,
                "splits": [{"split_index": oversized, "elapsed_seconds": 1}],
            }],
        })[0]
        self.assertFalse("last_split_index" in invalid_index)
        self.assertEqual(invalid_index["splits"], [])

        # Keep the adversarial value below Python's integer-to-decimal digit
        # limit so a failed assertion remains an ordinary test failure.
        enormous_identifier = 10 ** 100
        self.assertEqual(sanitize_results_payload({
            "results": [{"id": enormous_identifier}],
        }), [])
        self.assertEqual(sanitize_leaderboard_payload({
            "leaderboard": [{"id": enormous_identifier}],
        }), [])

    def test_result_and_split_deduplication_and_caps_are_deterministic(self):
        first = result_row(0, name="First")
        duplicate = result_row(0, name="Second")
        rows = [first, duplicate]
        rows.extend({"id": runner_id, "name": f"Runner {runner_id}"}
                    for runner_id in range(1, MAX_RESULT_ROWS - 1))
        clean = sanitize_results_payload({"results": rows})
        self.assertEqual(len(rows), MAX_RESULT_ROWS)
        self.assertEqual(len(clean), MAX_RESULT_ROWS - 1)
        self.assertEqual(clean[0]["name"], "First")
        self.assertEqual(clean[-1]["id"], MAX_RESULT_ROWS - 2)

        split_row = result_row(3, split_count=1)
        split_row["splits"] = [
            {"split_index": 0, "elapsed_seconds": 0, "place": 1},
            {"split_index": 0, "elapsed_seconds": 999, "place": 999},
        ]
        splits = sanitize_results_payload({"results": [split_row]})[0]["splits"]
        self.assertEqual(splits, [
            {"split_index": 0, "elapsed_seconds": 0.0, "place": 1},
        ])

        boundary_row = {"id": 4, "splits": [
            {"split_index": index, "elapsed_seconds": float(index), "rank": index + 1}
            for index in range(MAX_SPLIT_READINGS)
        ]}
        boundary_splits = sanitize_results_payload({"results": [boundary_row]})[0]["splits"]
        self.assertEqual(len(boundary_splits), MAX_SPLIT_READINGS)
        self.assertEqual(boundary_splits[-1]["split_index"], MAX_SPLIT_READINGS - 1)


class CacheTests(unittest.TestCase):
    def make_cache(self, *, max_events=8, ttl=10):
        self.now = 0.0
        self.result_calls = []
        self.leaderboard_calls = []
        self.fail_results = set()
        self.fail_leaderboard = set()

        def load_results(event_id):
            self.result_calls.append(event_id)
            if event_id in self.fail_results:
                raise RuntimeError("PRIVATE_RAW_EXCEPTION_SENTINEL")
            return payload_for(event_id)

        def load_leaderboard(event_id):
            self.leaderboard_calls.append(event_id)
            if event_id in self.fail_leaderboard:
                raise RuntimeError("PRIVATE_RAW_EXCEPTION_SENTINEL")
            return {"status": "ok", "leaderboard": [{"id": 1, "is_anonymous": False}]}

        return CompletedResultsCache(
            results_loader=load_results,
            leaderboard_loader=load_leaderboard,
            ttl_seconds=ttl,
            max_events=max_events,
            clock=lambda: self.now,
        )

    def test_fresh_ttl_path_and_public_reads_are_deep_copies(self):
        cache = self.make_cache()
        first = cache.get("event-one")
        first["results"][0]["name"] = "caller mutation"
        first["results"][0]["splits"][0]["elapsed_seconds"] = 999
        first["leaderboard"].append({"id": 999})
        self.now = 9.999
        second = cache.get("event-one")

        self.assertEqual(self.result_calls, ["event-one"])
        self.assertEqual(self.leaderboard_calls, ["event-one"])
        self.assertEqual(second["results"][0]["name"], "Public event-one")
        self.assertEqual(second["results"][0]["splits"][0]["elapsed_seconds"], 12.5)
        self.assertEqual(second["leaderboard"], [{"id": 1, "is_anonymous": False}])
        self.assertFalse(cache.stale)

    def test_cache_internal_values_and_returned_keys_contain_no_private_data(self):
        cache = self.make_cache()
        clean = cache.get("event-one")
        self.assertEqual(set(clean), {"results", "leaderboard"})
        assert_private_free(self, clean)
        assert_private_free(self, cache._values)
        self.assertNotIn("PRIVATE_EMAIL_SENTINEL", repr(cache._values))
        for value in cache._values.values():
            self.assertEqual(set(value), {"results", "leaderboard"})

    def test_leaderboard_anonymity_is_applied_before_cache_assignment(self):
        now = [0.0]

        def results(_event_id):
            row = result_row(1, name="PRIVATE_ANONYMOUS_NAME")
            row["bib"] = "PRIVATE_ANONYMOUS_BIB"
            return {"results": [row]}

        cache = CompletedResultsCache(
            results_loader=results,
            leaderboard_loader=lambda _event_id: {
                "leaderboard": [{"id": "1", "athlete_anonymous": True}]
            },
            clock=lambda: now[0],
        )
        clean = cache.get("event-one")
        self.assertNotIn("name", clean["results"][0])
        self.assertNotIn("bib", clean["results"][0])
        assert_private_free(self, cache._values)

    def test_65_digit_integer_and_string_ids_are_rejected_symmetrically(self):
        identifier_text = "9" * 65
        identifier_int = int(identifier_text)
        cache = CompletedResultsCache(
            results_loader=lambda _event_id: {"results": [{
                "id": identifier_int,
                "name": "PRIVATE_ANONYMOUS_NAME",
                "bib": "PRIVATE_ANONYMOUS_BIB",
            }]},
            leaderboard_loader=lambda _event_id: {"leaderboard": [{
                "id": identifier_text,
                "athlete_anonymous": True,
            }]},
            clock=lambda: 0.0,
        )

        clean = cache.get("event-one")

        self.assertEqual(clean, {"results": [], "leaderboard": []})
        assert_private_free(self, cache._values)
        self.assertNotIn("PRIVATE_ANONYMOUS_NAME", repr(cache._values))
        self.assertNotIn("PRIVATE_ANONYMOUS_BIB", repr(cache._values))

    def test_expired_reload_failure_returns_only_sanitized_stale_copy(self):
        cache = self.make_cache(ttl=10)
        expected = cache.get("event-one")
        self.now = 10.0
        self.fail_results.add("event-one")

        stale = cache.get("event-one")

        self.assertEqual(stale, expected)
        self.assertIsNot(stale, expected)
        self.assertTrue(cache.stale)
        self.assertNotIn("PRIVATE_RAW_EXCEPTION_SENTINEL", repr(stale))
        stale["results"].clear()
        self.assertTrue(cache.get("event-one")["results"])
        self.assertTrue(cache.stale)

    def test_successful_expired_reload_clears_stale_flag(self):
        cache = self.make_cache(ttl=5)
        cache.get("event-one")
        self.now = 5.0
        self.fail_results.add("event-one")
        cache.get("event-one")
        self.assertTrue(cache.stale)
        self.fail_results.clear()
        self.now = 6.0
        cache.get("event-one")
        self.assertFalse(cache.stale)
        self.assertEqual(self.result_calls, ["event-one", "event-one", "event-one"])

    def test_cold_failure_is_generic_and_does_not_cache_partial_results(self):
        cache = self.make_cache()
        self.fail_leaderboard.add("event-one")
        with self.assertRaises(CompletedResultsUnavailable) as caught:
            cache.get("event-one")
        self.assertEqual(str(caught.exception), "completed results unavailable")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("PRIVATE_RAW_EXCEPTION_SENTINEL", repr(caught.exception))
        self.assertEqual(cache._values, {})

    def test_failure_for_one_event_never_falls_back_to_another_event(self):
        cache = self.make_cache()
        cache.get("event-one")
        self.fail_results.add("event-two")
        with self.assertRaises(CompletedResultsUnavailable):
            cache.get("event-two")
        self.assertEqual(list(cache._values), ["event-one"])

    def test_lru_eviction_is_bounded_and_access_ordered(self):
        cache = self.make_cache(max_events=2)
        cache.get("event-one")
        cache.get("event-two")
        cache.get("event-one")
        cache.get("event-three")
        self.assertEqual(list(cache._values), ["event-one", "event-three"])
        cache.get("event-two")
        self.assertEqual(self.result_calls,
                         ["event-one", "event-two", "event-three", "event-two"])
        self.assertEqual(len(cache._values), 2)

    def test_configured_capacity_can_never_exceed_eight_events(self):
        cache = self.make_cache(max_events=99)
        for index in range(9):
            cache.get(f"event-{index}")
        self.assertEqual(len(cache._values), 8)
        self.assertEqual(list(cache._values), [f"event-{index}" for index in range(1, 9)])

    def test_invalid_identifier_is_rejected_before_clock_or_loaders(self):
        touched = []
        cache = CompletedResultsCache(
            results_loader=lambda value: touched.append(("results", value)),
            leaderboard_loader=lambda value: touched.append(("leaderboard", value)),
            clock=lambda: touched.append(("clock", None)),
        )
        for event_id in ("../event", "https://evil.test/x", "event/other", "UPPER", "event?x=1", ""):
            with self.subTest(event_id=event_id), self.assertRaises(InvalidIdentifier):
                cache.get(event_id)
        self.assertEqual(touched, [])

    def test_clock_is_sampled_inside_state_lock_and_expiry_uses_that_sample(self):
        now = [0.0]
        sampled = threading.Event()
        result_calls = []

        def clock():
            sampled.set()
            return now[0]

        cache = CompletedResultsCache(
            results_loader=lambda event_id: result_calls.append(event_id) or payload_for(event_id),
            leaderboard_loader=lambda _event_id: {"leaderboard": []},
            ttl_seconds=10,
            clock=clock,
        )
        cache.get("event-one")
        sampled.clear()
        now[0] = 9.0
        result = []
        worker = threading.Thread(target=lambda: result.append(cache.get("event-one")))

        cache._lock.acquire()
        try:
            worker.start()
            self.assertFalse(sampled.wait(0.05))
            now[0] = 10.0
        finally:
            cache._lock.release()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        self.assertEqual(result_calls, ["event-one", "event-one"])

    def test_unrelated_event_hits_and_loads_do_not_wait_for_blocked_io(self):
        entered = threading.Event()
        release = threading.Event()
        outcomes = {}

        def load_results(event_id):
            if event_id == "event-one":
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test loader timed out")
            return payload_for(event_id)

        cache = CompletedResultsCache(
            results_loader=load_results,
            leaderboard_loader=lambda _event_id: {"leaderboard": []},
            clock=lambda: 0.0,
        )
        cache.get("event-two")
        blocked = threading.Thread(
            target=lambda: outcomes.setdefault("event-one", cache.get("event-one")),
        )
        hit = threading.Thread(
            target=lambda: outcomes.setdefault("event-two", cache.get("event-two")),
        )
        load = threading.Thread(
            target=lambda: outcomes.setdefault("event-three", cache.get("event-three")),
        )

        blocked.start()
        self.assertTrue(entered.wait(1))
        try:
            hit.start()
            load.start()
            hit.join(0.5)
            load.join(0.5)
            self.assertFalse(hit.is_alive())
            self.assertFalse(load.is_alive())
        finally:
            release.set()
            blocked.join(1)
            hit.join(1)
            load.join(1)
        self.assertEqual(set(outcomes), {"event-one", "event-two", "event-three"})

    def test_same_event_callers_share_one_inflight_load(self):
        entered = threading.Event()
        release = threading.Event()
        calls = {"results": 0, "leaderboard": 0}
        outcomes = []

        def load_results(event_id):
            calls["results"] += 1
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test loader timed out")
            return payload_for(event_id)

        def load_leaderboard(_event_id):
            calls["leaderboard"] += 1
            return {"leaderboard": []}

        cache = CompletedResultsCache(
            results_loader=load_results,
            leaderboard_loader=load_leaderboard,
            clock=lambda: 0.0,
        )
        workers = [threading.Thread(target=lambda: outcomes.append(cache.get("event-one")))
                   for _ in range(6)]
        for worker in workers:
            worker.start()
        self.assertTrue(entered.wait(1))
        release.set()
        for worker in workers:
            worker.join(1)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(outcomes), 6)
        self.assertEqual(calls, {"results": 1, "leaderboard": 1})
        self.assertEqual(outcomes, [outcomes[0]] * 6)
        self.assertEqual(len({id(value) for value in outcomes}), 6)

    def test_base_exception_during_load_releases_waiters_and_allows_retry(self):
        class LoadInterrupted(BaseException):
            pass

        entered = threading.Event()
        release = threading.Event()
        waiter_entered = threading.Event()
        calls = []
        leader_errors = []
        waiter_errors = []

        def load_results(event_id):
            calls.append(event_id)
            if len(calls) == 1:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test loader timed out")
                raise LoadInterrupted()
            return payload_for(event_id)

        cache = CompletedResultsCache(
            results_loader=load_results,
            leaderboard_loader=lambda _event_id: {"leaderboard": []},
            clock=lambda: 0.0,
        )

        def read_as_leader():
            try:
                cache.get("event-one")
            except BaseException as error:
                leader_errors.append(error)

        def read_as_waiter():
            try:
                cache.get("event-one")
            except BaseException as error:
                waiter_errors.append(error)

        leader = threading.Thread(target=read_as_leader)
        leader.start()
        self.assertTrue(entered.wait(1))

        flight = cache._flights["event-one"]
        original_wait = flight.done.wait

        def observed_wait(timeout=None):
            waiter_entered.set()
            return original_wait(timeout)

        flight.done.wait = observed_wait
        waiter = threading.Thread(target=read_as_waiter, daemon=True)
        waiter.start()
        self.assertTrue(waiter_entered.wait(1))
        release.set()
        leader.join(1)
        waiter.join(1)

        self.assertFalse(leader.is_alive())
        self.assertFalse(waiter.is_alive())
        self.assertEqual(len(leader_errors), 1)
        self.assertIsInstance(leader_errors[0], LoadInterrupted)
        self.assertEqual(len(waiter_errors), 1)
        self.assertIsInstance(waiter_errors[0], CompletedResultsUnavailable)
        self.assertEqual(cache._flights, {})

        self.assertEqual(cache.get("event-one")["results"][0]["name"], "Public event-one")
        self.assertEqual(calls, ["event-one", "event-one"])

    def test_same_event_stale_fallback_is_single_flight(self):
        now = [0.0]
        entered = threading.Event()
        release = threading.Event()
        result_calls = []
        fail = [False]
        outcomes = []
        stale_flags = []

        def load_results(event_id):
            result_calls.append(event_id)
            if fail[0]:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test loader timed out")
                raise RuntimeError("PRIVATE_RAW_EXCEPTION_SENTINEL")
            return payload_for(event_id)

        cache = CompletedResultsCache(
            results_loader=load_results,
            leaderboard_loader=lambda _event_id: {"leaderboard": []},
            ttl_seconds=5,
            clock=lambda: now[0],
        )
        expected = cache.get("event-one")
        now[0] = 5.0
        fail[0] = True

        def read_stale():
            outcomes.append(cache.get("event-one"))
            stale_flags.append(cache.stale)

        workers = [threading.Thread(target=read_stale) for _ in range(6)]
        for worker in workers:
            worker.start()
        self.assertTrue(entered.wait(1))
        release.set()
        for worker in workers:
            worker.join(1)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(result_calls, ["event-one", "event-one"])
        self.assertEqual(outcomes, [expected] * 6)
        self.assertEqual(stale_flags, [True] * 6)

    def test_backward_clock_never_makes_an_old_entry_fresh(self):
        cache = self.make_cache(ttl=10)
        self.now = 100.0
        cache.get("event-one")
        self.now = 90.0

        cache.get("event-one")

        self.assertEqual(self.result_calls, ["event-one", "event-one"])
        self.assertFalse(cache.stale)

    def test_failed_stale_refresh_after_clock_rollback_cannot_postpone_due_work(self):
        cache = self.make_cache(ttl=10)
        expected = cache.get("event-one")
        self.fail_results.add("event-one")

        for now in (10.0, 5.0, 9.0):
            self.now = now
            self.assertEqual(cache.get("event-one"), expected)
            self.assertTrue(cache.stale)

        self.assertEqual(self.result_calls, ["event-one"] * 4)

    def test_rollback_refresh_obligation_persists_until_that_event_reloads(self):
        cache = self.make_cache(ttl=10)
        self.now = 100.0
        expected_one = cache.get("event-one")
        expected_two = cache.get("event-two")
        self.fail_results.add("event-one")

        self.now = 90.0
        self.assertEqual(cache.get("event-one"), expected_one)
        self.assertTrue(cache.stale)
        self.assertEqual(self.result_calls, ["event-one", "event-two", "event-one"])

        self.now = 91.0
        self.assertEqual(cache.get("event-two"), expected_two)
        self.assertFalse(cache.stale)
        self.assertEqual(self.result_calls, ["event-one", "event-two", "event-one"])

        self.assertEqual(cache.get("event-one"), expected_one)
        self.assertTrue(cache.stale)
        self.assertEqual(
            self.result_calls,
            ["event-one", "event-two", "event-one", "event-one"],
        )

        self.fail_results.clear()
        self.now = 92.0
        self.assertEqual(cache.get("event-one"), expected_one)
        self.assertFalse(cache.stale)
        self.assertEqual(
            self.result_calls,
            ["event-one", "event-two", "event-one", "event-one", "event-one"],
        )

        self.now = 93.0
        self.assertEqual(cache.get("event-one"), expected_one)
        self.assertFalse(cache.stale)
        self.assertEqual(
            self.result_calls,
            ["event-one", "event-two", "event-one", "event-one", "event-one"],
        )

    def test_stale_status_is_caller_specific(self):
        cache = self.make_cache(ttl=5)
        cache.get("event-one")
        self.now = 5.0
        self.fail_results.add("event-one")
        stale_returned = threading.Event()
        fresh_returned = threading.Event()
        statuses = {}

        def stale_reader():
            cache.get("event-one")
            stale_returned.set()
            self.assertTrue(fresh_returned.wait(1))
            statuses["stale-caller"] = cache.stale

        worker = threading.Thread(target=stale_reader)
        worker.start()
        self.assertTrue(stale_returned.wait(1))
        cache.get("event-two")
        statuses["fresh-caller"] = cache.stale
        fresh_returned.set()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(statuses, {"stale-caller": True, "fresh-caller": False})

    def test_injected_loader_payloads_receive_the_same_structural_bounds(self):
        leaderboard_touched = []
        cache = CompletedResultsCache(
            results_loader=lambda _event_id: {"results": [None] * (MAX_RESULT_ROWS + 1)},
            leaderboard_loader=lambda event_id: leaderboard_touched.append(event_id),
            clock=lambda: 0.0,
        )

        with self.assertRaises(CompletedResultsUnavailable):
            cache.get("event-one")
        self.assertEqual(leaderboard_touched, [])


class DefaultFetcherTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, body, status=200, final_url=None):
            self.body = body
            self.status = status
            self.final_url = final_url
            self.read_sizes = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            self.read_sizes.append(size)
            return self.body

        def geturl(self):
            return self.final_url

    def test_results_fetch_uses_only_validated_fixed_host_path_and_bounded_read(self):
        expected_url = API_HOST + "/events/event-one/results"
        response = self.FakeResponse(
            json.dumps({"status": "ok", "results": []}).encode(),
            final_url=expected_url,
        )
        with patch("completed_results._open_fixed_host", return_value=response) as opener:
            payload = fetch_results_payload("event-one")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, expected_url)
        self.assertEqual(response.read_sizes, [MAX_RESPONSE_BYTES + 1])
        self.assertEqual(payload["results"], [])

    def test_results_fetch_rejects_oversized_body_and_malformed_id_generically(self):
        response = self.FakeResponse(
            b"x" * (MAX_RESPONSE_BYTES + 1),
            final_url=API_HOST + "/events/event-one/results",
        )
        with patch("completed_results._open_fixed_host", return_value=response):
            with self.assertRaises(CompletedResultsUnavailable) as caught:
                fetch_results_payload("event-one")
        self.assertEqual(str(caught.exception), "completed results unavailable")
        self.assertIsNone(caught.exception.__context__)
        with patch("completed_results._open_fixed_host") as opener:
            with self.assertRaises(InvalidIdentifier):
                fetch_results_payload("https://evil.test/private")
            opener.assert_not_called()

    def test_results_fetch_validates_final_origin_before_reading(self):
        expected_url = API_HOST + "/events/event-one/results"
        body = json.dumps({"status": "ok", "results": []}).encode()
        same_origin = self.FakeResponse(body, final_url=expected_url)
        with patch("completed_results._open_fixed_host", return_value=same_origin):
            self.assertEqual(fetch_results_payload("event-one")["results"], [])
        self.assertEqual(same_origin.read_sizes, [MAX_RESPONSE_BYTES + 1])

        evil_final_urls = (
            "https://evil.test/events/event-one/results",
            "http://api.competitivetiming.com/events/event-one/results",
            "https://api.competitivetiming.com.evil.test/events/event-one/results",
        )
        for final_url in evil_final_urls:
            response = self.FakeResponse(body, final_url=final_url)
            with self.subTest(final_url=final_url), \
                    patch("completed_results._open_fixed_host", return_value=response), \
                    self.assertRaises(CompletedResultsUnavailable):
                fetch_results_payload("event-one")
            self.assertEqual(response.read_sizes, [])

    def test_redirect_policy_rejects_cross_origin_before_following(self):
        expected_url = API_HOST + "/events/event-one/results"
        request = completed_results_module.urllib.request.Request(expected_url)
        handler = completed_results_module._FixedHostRedirectHandler()
        response = BytesIO()
        headers = HTTPMessage()

        redirected = handler.redirect_request(
            request, response, 302, "Found", headers, expected_url + "?canonical=1",
        )
        assert redirected is not None
        self.assertEqual(redirected.full_url, expected_url + "?canonical=1")

        for final_url in (
            "https://evil.test/events/event-one/results",
            "http://api.competitivetiming.com/events/event-one/results",
        ):
            with self.subTest(final_url=final_url), \
                    self.assertRaises(CompletedResultsUnavailable):
                handler.redirect_request(request, response, 302, "Found", headers, final_url)

    def test_fixed_origin_rejects_url_authority_lookalikes(self):
        path = "/events/event-one/results"
        self.assertTrue(completed_results_module._is_fixed_api_origin(API_HOST + path))
        self.assertTrue(completed_results_module._is_fixed_api_origin(
            "https://api.competitivetiming.com:443" + path,
        ))
        for url in (
            "https://user@api.competitivetiming.com" + path,
            "https://user:password@api.competitivetiming.com" + path,
            "https://api.competitivetiming.com:444" + path,
            "//api.competitivetiming.com" + path,
            "https://api.competitivetiming.com." + path,
            "https://api%2ecompetitivetiming.com" + path,
            "https://%61pi.competitivetiming.com" + path,
        ):
            with self.subTest(url=url):
                self.assertFalse(completed_results_module._is_fixed_api_origin(url))

    def test_leaderboard_pages_use_bounded_fixed_paths_and_cumulative_body_budget(self):
        first_url = (API_HOST
                     + f"/events/event-one/leaderboard?limit={LEADERBOARD_PAGE_SIZE}&offset=0")
        second_url = (API_HOST
                      + f"/events/event-one/leaderboard?limit={LEADERBOARD_PAGE_SIZE}&offset=1")
        first_body = json.dumps({
            "status": "ok", "leaderboard": [{"id": 1}], "count": 1,
            "total": 2, "has_more": True,
        }).encode()
        second_body = json.dumps({
            "status": "ok", "leaderboard": [{"id": 2}], "count": 1,
            "total": 2, "has_more": False,
        }).encode()
        responses = [
            self.FakeResponse(first_body, final_url=first_url),
            self.FakeResponse(second_body, final_url=second_url),
        ]
        with patch("completed_results._open_fixed_host", side_effect=responses) as opener:
            payload = fetch_leaderboard_payload("event-one")

        urls = [call.args[0].full_url for call in opener.call_args_list]
        self.assertEqual(urls, [first_url, second_url])
        self.assertEqual(responses[0].read_sizes, [MAX_RESPONSE_BYTES + 1])
        self.assertEqual(responses[1].read_sizes, [MAX_RESPONSE_BYTES - len(first_body) + 1])
        self.assertEqual(payload["leaderboard"], [{"id": 1}, {"id": 2}])

    def test_leaderboard_rejects_oversized_pages_and_wrong_page_count(self):
        url = (API_HOST
               + f"/events/event-one/leaderboard?limit={LEADERBOARD_PAGE_SIZE}&offset=0")
        malformed_payloads = (
            {
                "leaderboard": [{"id": index} for index in range(LEADERBOARD_PAGE_SIZE + 1)],
                "has_more": False,
            },
            {"leaderboard": [{"id": 1}], "count": 2, "has_more": False},
            {"leaderboard": [], "count": 10 ** 100, "has_more": False},
            {"leaderboard": [], "total": 100_001, "has_more": False},
        )
        for payload in malformed_payloads:
            response = self.FakeResponse(json.dumps(payload).encode(), final_url=url)
            with self.subTest(keys=tuple(payload)), \
                    patch("completed_results._open_fixed_host", return_value=response), \
                    self.assertRaises(CompletedResultsUnavailable):
                fetch_leaderboard_payload("event-one")

    def test_leaderboard_has_more_is_strict_and_absence_never_continues(self):
        url = (API_HOST
               + f"/events/event-one/leaderboard?limit={LEADERBOARD_PAGE_SIZE}&offset=0")
        for malformed in (None, 0, 1, "false", "true"):
            body = json.dumps({"leaderboard": [], "has_more": malformed}).encode()
            response = self.FakeResponse(body, final_url=url)
            with self.subTest(malformed=malformed), \
                    patch("completed_results._open_fixed_host", return_value=response), \
                    self.assertRaises(CompletedResultsUnavailable):
                fetch_leaderboard_payload("event-one")

        incomplete = self.FakeResponse(
            json.dumps({"leaderboard": [{"id": 1}], "total": 2}).encode(),
            final_url=url,
        )
        with patch("completed_results._open_fixed_host", return_value=incomplete) as opener:
            with self.assertRaises(CompletedResultsUnavailable):
                fetch_leaderboard_payload("event-one")
        self.assertEqual(opener.call_count, 1)

        complete = self.FakeResponse(
            json.dumps({"leaderboard": [{"id": 1}], "total": 1}).encode(),
            final_url=url,
        )
        with patch("completed_results._open_fixed_host", return_value=complete) as opener:
            payload = fetch_leaderboard_payload("event-one")
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(payload["leaderboard"], [{"id": 1}])

    def test_leaderboard_without_metadata_continues_full_pages_until_a_short_page(self):
        first_rows = [{"id": index + 1} for index in range(LEADERBOARD_PAGE_SIZE)]
        second_rows = [{"id": LEADERBOARD_PAGE_SIZE + 1}]
        first_url = (API_HOST + "/events/event-one/leaderboard"
                     + f"?limit={LEADERBOARD_PAGE_SIZE}&offset=0")
        second_url = (API_HOST + "/events/event-one/leaderboard"
                      + f"?limit={LEADERBOARD_PAGE_SIZE}&offset={LEADERBOARD_PAGE_SIZE}")
        responses = [
            self.FakeResponse(json.dumps({"status": "ok", "leaderboard": first_rows}).encode(),
                              final_url=first_url),
            self.FakeResponse(json.dumps({"status": "ok", "leaderboard": second_rows}).encode(),
                              final_url=second_url),
        ]
        with patch("completed_results._open_fixed_host", side_effect=responses) as opener:
            payload = fetch_leaderboard_payload("event-one")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(len(payload["leaderboard"]), LEADERBOARD_PAGE_SIZE + 1)
        self.assertEqual(payload["leaderboard"][-1], second_rows[0])

    def test_leaderboard_rejects_repeated_full_and_partial_pages_by_identity(self):
        scenarios = (
            (
                [{"id": index, "display": "first"}
                 for index in range(LEADERBOARD_PAGE_SIZE)],
                [{"id": index, "display": "irrelevant field changed"}
                 for index in range(LEADERBOARD_PAGE_SIZE)],
            ),
            (
                [{"id": 1, "display": "first"}, {"id": 2, "display": "first"}],
                [{"id": 1, "display": "changed"}, {"id": 2, "display": "changed"}],
            ),
        )
        for first_rows, repeated_rows in scenarios:
            total = len(first_rows) + len(repeated_rows)
            first_offset = 0
            second_offset = len(first_rows)
            first_url = (API_HOST + "/events/event-one/leaderboard"
                         + f"?limit={LEADERBOARD_PAGE_SIZE}&offset={first_offset}")
            second_url = (API_HOST + "/events/event-one/leaderboard"
                          + f"?limit={LEADERBOARD_PAGE_SIZE}&offset={second_offset}")
            first = self.FakeResponse(json.dumps({
                "leaderboard": first_rows, "count": len(first_rows),
                "total": total, "has_more": True,
            }).encode(), final_url=first_url)
            second = self.FakeResponse(json.dumps({
                "leaderboard": repeated_rows, "count": len(repeated_rows),
                "total": total, "has_more": False,
            }).encode(), final_url=second_url)
            with self.subTest(page_size=len(first_rows)), \
                    patch("completed_results._open_fixed_host", side_effect=[first, second]), \
                    self.assertRaises(CompletedResultsUnavailable):
                fetch_leaderboard_payload("event-one")

    def test_leaderboard_unique_cardinality_must_match_declared_total(self):
        url = (API_HOST
               + f"/events/event-one/leaderboard?limit={LEADERBOARD_PAGE_SIZE}&offset=0")
        malformed = self.FakeResponse(json.dumps({
            "leaderboard": [
                {"id": "01", "is_anonymous": False},
                {"runner_id": 1, "athlete_anonymous": True},
            ],
            "count": 2,
            "total": 2,
            "has_more": False,
        }).encode(), final_url=url)
        with patch("completed_results._open_fixed_host", return_value=malformed), \
                self.assertRaises(CompletedResultsUnavailable):
            fetch_leaderboard_payload("event-one")

    def test_leaderboard_sequence_has_a_total_wall_clock_budget(self):
        page = {
            "leaderboard": [{"id": 1}],
            "count": 1,
            "total": 1,
            "has_more": False,
        }
        with patch("completed_results.time.monotonic", side_effect=[
                0.0, 0.0, MAX_LEADERBOARD_LOAD_SECONDS + 0.001,
        ]), patch("completed_results._read_fixed_host_json", return_value=(page, 10)) as reader:
            with self.assertRaises(CompletedResultsUnavailable):
                fetch_leaderboard_payload("event-one")
        self.assertEqual(reader.call_count, 1)
        self.assertGreater(reader.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(reader.call_args.kwargs["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
