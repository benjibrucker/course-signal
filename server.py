#!/usr/bin/env python3
"""Local read-only proxy and estimator for The Rut 2026 live map."""

from __future__ import annotations

import argparse
import bisect
import contextlib
import copy
import concurrent.futures
import dataclasses
import datetime as dt
import functools
import json
import math
import mimetypes
import os
import re
import socket
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import competitive_catalog as catalog
from completed_results import CompletedResultsCache
from result_analysis import analyze_runner, search_public_runners
from terrain_model import build_profile, predict

from finish_metrics import (
    after_seconds, coordinate, finite_number, great_circle_m, match_route,
    parse_timestamp, prepare_route, timestamp,
)

APP_NAME = "Rut Live Map"
APP_VERSION = "1.6.0"
TERRAIN_PILOT_ENABLED = True
HISTORY_TTL_SECONDS = 60
TERRAIN_MODEL = "terrain-pilot-v1"
APP_ROOT = Path(__file__).resolve().parent
STATIC_FILES = frozenset({
    "index.html",
    "styles.css",
    "app.js",
    "course_signal_shell.js",
    "results_model.js",
    "results_view.js",
    "config.js",
    "race-logic.js",
    "elevation-profile.js",
    "favicon.svg",
    "rut-2026-aid-chart.png",
    "docs/energy-model.md",
    "vendor/leaflet/leaflet.js",
    "vendor/leaflet/leaflet.css",
    "vendor/leaflet/LICENSE",
})
UPSTREAM = "https://api.competitivetiming.com"
RESULTS_RACE_SLUG = "the-rut"
RESULTS_RACE_NAME = "The Rut"
RESULTS_EVENT_ID = "the-rut-28k-2026"
RESULTS_EVENT_LABEL = "28K"
EVENTS = (
    ("the-rut-50k-2026", "50K"),
    ("the-rut-28k-2026", "28K"),
    ("the-rut-21k-2026", "21K"),
    ("the-rut-11k-2026", "11K"),
    ("the-rut-vk-2026", "VK"),
)
EVENT_LABELS = dict(EVENTS)
RESULTS_EVENT_IDS = frozenset(EVENT_LABELS)
USER_AGENT = "RutLiveMap/1.0 (+local spectator display)"
GPS_TTL_SECONDS = 15
LEADERBOARD_TTL_SECONDS = 30
EVENT_TTL_SECONDS = 30
COURSE_TTL_SECONDS = 3600
# Maximum age since a successful fetch, inclusive. Dynamic Course Signal never
# uses the inherited unbounded stale policy.
DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS = 5 * 60
DYNAMIC_EVENT_MAX_STALE_SECONDS = 15 * 60
DYNAMIC_COURSE_MAX_STALE_SECONDS = 24 * 60 * 60
SOURCE_ASSEMBLY_GRACE_SECONDS = 5
FRESH_GPS_SECONDS = 90
LEADERBOARD_PAGE_SIZE = 500
MAX_LEADERBOARD_PAGES = 500
MAX_LEADERBOARD_UNIQUE_RESULTS = 100_000
MAX_LEADERBOARD_RESPONSE_ROWS = 100_000
MAX_LEADERBOARD_LOAD_SECONDS = 90
MAX_LEADERBOARD_RECORDS = MAX_LEADERBOARD_UNIQUE_RESULTS
MAX_DYNAMIC_CACHE_ENTRIES = 256
MAX_COURSE_MAPS = 16
MAX_COURSE_TRACK_POINTS = 100_000
MAX_COURSE_SPLIT_POINTS = 1_000
MAX_CHECKPOINT_FORECAST_SECONDS = 24 * 60 * 60
MAX_UPSTREAM_BYTES = 32 * 1024 * 1024
MAX_RESULT_RECORDS = 10_000
MAX_RESULT_SPLITS = 100
MAX_RESULTS_RUNNER_ID = 9_007_199_254_740_991
MAX_RESULTS_SEARCH_LIMIT = 20
_RESULTS_EVENT_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
RESULTS_ROUTES = frozenset({"results/search", "results/runner"})
RESULTS_PATHS = frozenset({"/api/results/search", "/api/results/runner"})

_RESULT_FIELDS = frozenset({
    "id", "runner_id", "event_id", "eventId", "name", "runner_name", "bib",
    "age", "gender", "age_group", "chip_age_group_place", "gun_age_group_place",
    "chip_gender_place", "gun_gender_place",
    "is_anonymous", "athlete_anonymous", "status", "dns", "dq", "dnq", "dnf",
    "dropped", "finish_time_seconds", "finish_place", "overall_place", "place",
    "gun_place", "chip_place", "chip_start_seconds", "last_split_index",
    "last_split_time",
})
_RESULT_SPLIT_FIELDS = frozenset({
    "split_index", "elapsed_seconds", "cumulative_place", "overall_place", "place",
    "rank", "segment_place", "split_place", "interval_place", "segment_rank",
})
_RESULT_PAYLOAD_FIELDS = frozenset({"status", "has_more", "total", "total_count", "count"})
_LEADERBOARD_FIELDS = frozenset({
    "id", "runner_id", "name", "runner_name", "bib", "is_anonymous", "athlete_anonymous",
    "status", "dns", "dq", "dnq", "dnf", "dropped", "finish_time_seconds",
    "last_split_index", "last_split_time", "chip_start_seconds",
    "estimated_finish_seconds", "goal_time_seconds", "gun_place", "chip_place", "has_gps",
})
_GPS_FIELDS = frozenset({
    "id", "runner_id", "runner_name", "bib", "is_anonymous", "athlete_anonymous",
    "status", "dns", "dq", "dnq", "dnf", "dropped", "latitude", "longitude",
    "recorded_at", "accuracy", "progress", "source",
})
_EVENT_FIELDS = frozenset({
    "id", "name", "display_name", "event_date", "start_time", "estimated_start_time",
    "timezone", "course_status", "course_distance_miles", "live_tracking_enabled",
})
_MULTIPLIER_FIELDS = frozenset({"split_index", "progress_pct", "pace_multiplier"})
_COURSE_FIELDS = frozenset({"id", "eventId", "event_id", "color"})
_TRACK_POINT_FIELDS = frozenset({"lat", "lng", "ele"})
_SPLIT_POINT_FIELDS = frozenset({"lat", "lng", "name", "index", "split_index", "splitIndex"})


@dataclasses.dataclass
class CacheEntry:
    value: Any
    fetched_at: float
    fetched_wall: float = dataclasses.field(default_factory=time.time)
    max_stale_seconds: float | None = None


@dataclasses.dataclass
class _KeyLockState:
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    users: int = 0


class CacheUnavailable(RuntimeError):
    """A bounded cache has neither a fresh nor an acceptably stale value."""


class JsonCache:
    """Small TTL cache with optional LRU and stale-on-error bounds.

    ``max_stale_seconds`` is measured from the successful fetch. Omitting it
    retains the inherited Rut cache's unbounded stale-on-error behavior.
    Omitting ``max_entries`` likewise preserves its unbounded-capacity default.
    """

    def __init__(self, clock: Callable[[], float] | None = None,
                 max_entries: int | None = None) -> None:
        if max_entries is not None and (type(max_entries) is not int or max_entries <= 0):
            raise ValueError("max_entries must be a positive integer")
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[str, _KeyLockState] = {}
        self._clock = clock
        self._max_entries = max_entries

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    @contextlib.contextmanager
    def _lock_for(self, key: str):
        with self._lock:
            state = self._key_locks.setdefault(key, _KeyLockState())
            state.users += 1
        state.lock.acquire()
        try:
            yield
        finally:
            state.lock.release()
            with self._lock:
                state.users -= 1
                if state.users == 0 and self._key_locks.get(key) is state:
                    self._key_locks.pop(key, None)

    def _cleanup_expired_locked(self, now: float, current_key: str) -> None:
        """Discard bounded stale values lazily without changing legacy policy."""
        for key, entry in list(self._entries.items()):
            if (key != current_key and entry.max_stale_seconds is not None
                    and now - entry.fetched_at > entry.max_stale_seconds):
                self._entries.pop(key, None)

    def _touch_locked(self, key: str, max_stale_seconds: float | None) -> None:
        entry = self._entries[key]
        if max_stale_seconds is not None:
            entry.max_stale_seconds = (max_stale_seconds if entry.max_stale_seconds is None
                                       else min(entry.max_stale_seconds, max_stale_seconds))
        self._entries.move_to_end(key)

    def _enforce_capacity_locked(self) -> None:
        if self._max_entries is not None:
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def source_fetched_at(self, key: str) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            return timestamp(dt.datetime.fromtimestamp(entry.fetched_wall, dt.timezone.utc)) if entry else None

    def get(self, key: str, ttl: int, loader: Callable[[], Any], *,
            max_stale_seconds: float | None = None) -> tuple[Any, bool]:
        if max_stale_seconds is not None and (
                isinstance(max_stale_seconds, bool) or not math.isfinite(max_stale_seconds)
                or max_stale_seconds < 0):
            raise ValueError("max_stale_seconds must be finite and nonnegative")
        now = self._now()
        with self._lock:
            self._cleanup_expired_locked(now, key)
            entry = self._entries.get(key)
            if entry and now - entry.fetched_at < ttl:
                self._touch_locked(key, max_stale_seconds)
                return entry.value, False

        with self._lock_for(key):
            now = self._now()
            with self._lock:
                self._cleanup_expired_locked(now, key)
                entry = self._entries.get(key)
                if entry and now - entry.fetched_at < ttl:
                    self._touch_locked(key, max_stale_seconds)
                    return entry.value, False
            try:
                value = loader()
            except Exception:
                with self._lock:
                    stale = self._entries.get(key)
                age = self._now() - stale.fetched_at if stale is not None else None
                if stale is not None and (max_stale_seconds is None or (
                        age is not None and 0 <= age <= max_stale_seconds)):
                    with self._lock:
                        if key in self._entries:
                            self._touch_locked(key, max_stale_seconds)
                    return stale.value, True
                if max_stale_seconds is not None:
                    raise CacheUnavailable("cached source unavailable") from None
                raise
            with self._lock:
                self._entries[key] = CacheEntry(
                    value=value, fetched_at=self._now(), max_stale_seconds=max_stale_seconds,
                )
                self._entries.move_to_end(key)
                self._enforce_capacity_locked()
            return value, False


CACHE = JsonCache(max_entries=MAX_DYNAMIC_CACHE_ENTRIES)
CATALOG = catalog.CatalogCache()
RESULTS_CACHE = CompletedResultsCache(max_events=len(EVENTS))


class SelectedEventUnavailable(RuntimeError):
    """Selected event source did not match the allowlisted catalog event."""


class UpstreamUnavailable(RuntimeError):
    """Generic fixed-origin fetch failure without response details."""


def _upstream_unavailable() -> UpstreamUnavailable:
    return UpstreamUnavailable("upstream unavailable")


def _is_fixed_upstream_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.competitivetiming.com"
        and parsed.netloc in {"api.competitivetiming.com", "api.competitivetiming.com:443"}
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )


class _FixedUpstreamRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject unsafe redirects before urllib can issue the next request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_fixed_upstream_url(newurl):
            raise _upstream_unavailable()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_ORIGINAL_URLOPEN = urllib.request.urlopen


class _ExpectedUrlResponse:
    """Give injected legacy test transports the URL real responses expose."""

    def __init__(self, response: Any, final_url: str) -> None:
        self._response = response
        self._final_url = final_url

    @property
    def status(self):
        return getattr(self._response, "status", None)

    def geturl(self) -> str:
        return self._final_url

    def read(self, size: int = -1):
        return self._response.read(size)

    def __enter__(self):
        enter = getattr(self._response, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *args):
        exit_method = getattr(self._response, "__exit__", None)
        if exit_method is not None:
            return exit_method(*args)
        close = getattr(self._response, "close", None)
        if close is not None:
            close()
        return None


def _open_fixed_upstream(request: urllib.request.Request, timeout: float):
    # Existing tests inject urllib.request.urlopen. The production stdlib path
    # always uses a dedicated opener with the fixed-origin redirect policy.
    if urllib.request.urlopen is not _ORIGINAL_URLOPEN:
        response = urllib.request.urlopen(request, timeout=timeout)
        return (response if callable(getattr(response, "geturl", None))
                else _ExpectedUrlResponse(response, request.full_url))
    opener = urllib.request.build_opener(_FixedUpstreamRedirectHandler())
    return opener.open(request, timeout=timeout)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _result_scalar(value: Any) -> Any:
    """Keep source scalars only; nested private data never enters result caches."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value is None or isinstance(value, (bool, int, float, str)) else None


def _cache_public_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and abs(value) <= 2 ** 53:
        return int(value)
    return None


def _sanitize_participant_record(raw: dict[str, Any], fields: frozenset[str],
                                 numeric_id: str) -> dict[str, Any]:
    clean = {field: _result_scalar(raw[field]) for field in fields if field in raw}
    for identifier_field in {numeric_id, "id", "runner_id"}:
        if identifier_field in clean:
            clean[identifier_field] = _cache_public_id(clean[identifier_field])
    for flag in ("is_anonymous", "athlete_anonymous"):
        if flag in raw:
            # Malformed truthy privacy evidence fails toward anonymity while its
            # nested contents are discarded.
            clean[flag] = bool(raw[flag])
    if clean.get("is_anonymous") or clean.get("athlete_anonymous"):
        for field in ("name", "runner_name"):
            if field in clean:
                clean[field] = "Anonymous Participant"
        if "bib" in clean:
            clean["bib"] = None
    return clean


def _participant_record_id(record: dict[str, Any]) -> int | None:
    """Resolve equivalent result ID forms without trusting conflicting aliases."""
    identifiers = [
        _cache_public_id(record[field])
        for field in ("id", "runner_id")
        if field in record and record[field] is not None
    ]
    if not identifiers or any(identifier is None for identifier in identifiers):
        return None
    return identifiers[0] if all(identifier == identifiers[0] for identifier in identifiers) else None


def _apply_anonymity_union(rows: list[Any]) -> None:
    """Scrub every matching row when any sanitized duplicate is anonymous."""
    anonymous_ids = {
        identifier
        for row in rows
        if isinstance(row, dict)
        and (row.get("is_anonymous") or row.get("athlete_anonymous"))
        and (identifier := _participant_record_id(row)) is not None
    }
    for row in rows:
        if not isinstance(row, dict) or _participant_record_id(row) not in anonymous_ids:
            continue
        row["is_anonymous"] = True
        for field in ("name", "runner_name"):
            if field in row:
                row[field] = "Anonymous Participant"
        if "bib" in row:
            row["bib"] = None
        for field in (
                "age", "gender", "age_group", "chip_age_group_place",
                "gun_age_group_place", "chip_gender_place", "gun_gender_place"):
            row.pop(field, None)


def sanitize_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlist analysis fields before a bulk result response can be cached."""
    rows = payload.get("results")
    if not isinstance(rows, list) or len(rows) > MAX_RESULT_RECORDS:
        raise RuntimeError("unexpected upstream payload")
    if "has_more" in payload and (
            type(payload["has_more"]) is not bool or payload["has_more"] is True):
        # Bulk history has no pagination contract. Reject a connected prefix
        # before it can enter shared memory or become participant evidence.
        raise RuntimeError("unexpected upstream payload")
    output = {field: _result_scalar(payload[field]) for field in _RESULT_PAYLOAD_FIELDS if field in payload}
    clean_rows = []
    for row in rows:
        if not isinstance(row, dict):
            clean_rows.append(None)
            continue
        clean = _sanitize_participant_record(row, _RESULT_FIELDS, "id")
        splits = row.get("splits")
        if isinstance(splits, list) and len(splits) <= MAX_RESULT_SPLITS:
            clean["splits"] = [
                {field: _result_scalar(split[field]) for field in _RESULT_SPLIT_FIELDS if field in split}
                if isinstance(split, dict) else None
                for split in splits
            ]
        elif "splits" in row:
            clean["splits"] = None
        clean_rows.append(clean)
    # The complete response is the privacy boundary: duplicate rows can carry
    # stronger anonymity evidence than the first record for the same runner.
    _apply_anonymity_union(clean_rows)
    output["results"] = clean_rows
    return output


def _leaderboard_page_count_is_valid(payload: dict[str, Any], rows: list[Any]) -> bool:
    """Validate optional per-page count without coercing booleans or numerics."""
    if "count" not in payload:
        return True
    count = payload["count"]
    return type(count) is int and 0 <= count <= LEADERBOARD_PAGE_SIZE and count == len(rows)


def sanitize_leaderboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlist leaderboard rows before any page reaches shared memory."""
    rows = payload.get("leaderboard")
    if not isinstance(rows, list) or len(rows) > LEADERBOARD_PAGE_SIZE:
        raise RuntimeError("unexpected upstream payload")
    if "has_more" in payload and type(payload["has_more"]) is not bool:
        raise RuntimeError("unexpected upstream payload")
    if not _leaderboard_page_count_is_valid(payload, rows):
        raise RuntimeError("unexpected upstream payload")
    output = {field: _result_scalar(payload[field])
              for field in _RESULT_PAYLOAD_FIELDS if field in payload}
    clean_rows = [
        _sanitize_participant_record(row, _LEADERBOARD_FIELDS, "id")
        if isinstance(row, dict) else None
        for row in rows
    ]
    _apply_anonymity_union(clean_rows)
    output["leaderboard"] = clean_rows
    return output


def sanitize_gps_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain route fixes and legacy telemetry, never arbitrary device data."""
    rows = payload.get("positions")
    if not isinstance(rows, list) or len(rows) > MAX_LEADERBOARD_RECORDS:
        raise RuntimeError("unexpected upstream payload")
    output = {field: _result_scalar(payload[field])
              for field in _RESULT_PAYLOAD_FIELDS if field in payload}
    clean_rows = [
        _sanitize_participant_record(row, _GPS_FIELDS, "runner_id")
        if isinstance(row, dict) else None
        for row in rows
    ]
    # Cache only the response-wide privacy union. A later canonical duplicate
    # must scrub every earlier alias before any value reaches shared memory.
    _apply_anonymity_union(clean_rows)
    output["positions"] = clean_rows
    return output


def sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only event timing/model metadata used by the live builder."""
    raw_event = payload.get("event")
    if not isinstance(raw_event, dict):
        raise RuntimeError("unexpected upstream payload")
    event = {field: _result_scalar(raw_event[field]) for field in _EVENT_FIELDS if field in raw_event}
    if "split_names" in raw_event:
        names = raw_event["split_names"]
        event["split_names"] = (
            [_result_scalar(name) for name in names]
            if isinstance(names, list) and len(names) <= MAX_RESULT_SPLITS else None
        )
    if "split_distances" in raw_event:
        distances = raw_event["split_distances"]
        split_count = len(event.get("split_names") or [])
        clean_distances = {}
        if isinstance(distances, dict) and len(distances) <= MAX_RESULT_SPLITS:
            for key, value in distances.items():
                if isinstance(key, bool) or not isinstance(key, (int, str)) or not isinstance(value, dict):
                    continue
                if isinstance(key, str):
                    if not key.isascii() or not key.isdecimal() or (len(key) > 1 and key.startswith("0")):
                        continue
                    try:
                        index = int(key)
                    except ValueError:
                        continue
                else:
                    index = key
                if 1 <= index < split_count:
                    clean_distances[str(index)] = {"value": _result_scalar(value.get("value"))}
        event["split_distances"] = clean_distances
    raw_multipliers = payload.get("multipliers")
    multipliers = [
        {field: _result_scalar(row[field]) for field in _MULTIPLIER_FIELDS if field in row}
        if isinstance(row, dict) else None
        for row in raw_multipliers
    ] if isinstance(raw_multipliers, list) and len(raw_multipliers) <= MAX_RESULT_SPLITS else []
    output = {field: _result_scalar(payload[field])
              for field in _RESULT_PAYLOAD_FIELDS if field in payload}
    output.update(event=event, multipliers=multipliers)
    return output


def _sanitize_points(raw: Any, fields: frozenset[str], maximum: int) -> list[Any]:
    if not isinstance(raw, list):
        return []
    if len(raw) > maximum:
        raise RuntimeError("unexpected upstream payload")
    return [
        {field: _result_scalar(point[field]) for field in fields if field in point}
        if isinstance(point, dict) else None
        for point in raw
    ]


def sanitize_course_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlist event-matched geometry without retaining unrelated records."""
    maps = payload.get("courseMaps")
    if not isinstance(maps, list) or len(maps) > MAX_COURSE_MAPS:
        raise RuntimeError("unexpected upstream payload")
    clean_maps = []
    for raw in maps:
        if not isinstance(raw, dict):
            clean_maps.append(None)
            continue
        course = {field: _result_scalar(raw[field]) for field in _COURSE_FIELDS if field in raw}
        course["trackPoints"] = _sanitize_points(
            raw.get("trackPoints"), _TRACK_POINT_FIELDS, MAX_COURSE_TRACK_POINTS,
        )
        course["splitPoints"] = _sanitize_points(
            raw.get("splitPoints"), _SPLIT_POINT_FIELDS, MAX_COURSE_SPLIT_POINTS,
        )
        clean_maps.append(course)
    output = {field: _result_scalar(payload[field])
              for field in _RESULT_PAYLOAD_FIELDS if field in payload}
    output["courseMaps"] = clean_maps
    return output


def sanitize_upstream_payload(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Select a narrow sanitizer for every dynamic selected-event source."""
    endpoint = urllib.parse.urlsplit(path).path
    if endpoint.endswith("/results"):
        return sanitize_result_payload(payload)
    if endpoint.endswith("/leaderboard"):
        return sanitize_leaderboard_payload(payload)
    if endpoint.startswith("/gps/locations/"):
        return sanitize_gps_payload(payload)
    if endpoint.startswith("/course-maps/event/"):
        return sanitize_course_payload(payload)
    if len(endpoint.strip("/").split("/")) == 2 and endpoint.startswith("/events/"):
        return sanitize_event_payload(payload)
    return payload


def _fetch_sanitized_upstream(path: str) -> dict[str, Any]:
    """Fetch one response and minimize it before it can reach shared memory."""
    if not path.startswith("/") or ".." in path:
        raise ValueError("invalid upstream path")
    url = UPSTREAM + path
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Origin": "https://competitivetiming.com",
            "Referer": "https://competitivetiming.com/",
            "User-Agent": USER_AGENT,
        },
    )
    failed = False
    output = None
    try:
        with _open_fixed_upstream(request, timeout=15) as response:
            # Validate the final origin before status/body inspection as a
            # second line of defense against alternate transports.
            if not _is_fixed_upstream_url(response.geturl()):
                raise _upstream_unavailable()
            if getattr(response, "status", None) != 200:
                raise _upstream_unavailable()
            body = response.read(MAX_UPSTREAM_BYTES + 1)
        if len(body) > MAX_UPSTREAM_BYTES:
            raise _upstream_unavailable()
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("status") not in (None, "ok"):
            raise _upstream_unavailable()
        output = sanitize_upstream_payload(path, payload)
    except Exception:
        # Raise after leaving the handler so __context__ cannot retain an
        # inspectable upstream exception or response body.
        failed = True
    if failed or output is None:
        raise _upstream_unavailable()
    return output


def upstream_json(path: str, ttl: int, *,
                  max_stale_seconds: float | None = None) -> tuple[dict[str, Any], bool]:
    if not path.startswith("/") or ".." in path:
        raise ValueError("invalid upstream path")
    return CACHE.get(
        UPSTREAM + path, ttl, lambda: _fetch_sanitized_upstream(path),
        max_stale_seconds=max_stale_seconds,
    )


def as_float(value: Any) -> float | None:
    return finite_number(value)


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and abs(value) <= 2 ** 53:
        return int(value)
    return None


def haversine_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    return great_circle_m((float(a["lat"]), float(a["lng"])), (float(b["lat"]), float(b["lng"])))


def route_distances(track: list[dict[str, Any]]) -> tuple[list[float], float]:
    route = prepare_route(tuple((point["lat"], point["lng"]) for point in track))
    return list(route.cumulative), route.total


def nearest_route_progress(
    point: dict[str, Any], track: list[dict[str, Any]], cumulative: list[float], total: float
) -> float | None:
    if not track or total <= 0:
        return None
    location = coordinate(point.get("lat"), point.get("lng"))
    if location is None:
        return None
    route = prepare_route(tuple((p["lat"], p["lng"]) for p in track))
    along, _ = match_route(location, route, 0.0, 1.0)
    return along / total if along is not None else None


def interpolate_route(
    track: list[dict[str, Any]], cumulative: list[float], total: float, progress: float
) -> tuple[float, float] | None:
    if not track:
        return None
    if len(track) == 1 or total <= 0:
        return float(track[0]["lat"]), float(track[0]["lng"])
    target = max(0.0, min(1.0, progress)) * total
    right = bisect.bisect_left(cumulative, target)
    if right <= 0:
        return float(track[0]["lat"]), float(track[0]["lng"])
    if right >= len(track):
        return float(track[-1]["lat"]), float(track[-1]["lng"])
    left = right - 1
    span = cumulative[right] - cumulative[left]
    fraction = 0.0 if span <= 0 else (target - cumulative[left]) / span
    lat = float(track[left]["lat"]) + (float(track[right]["lat"]) - float(track[left]["lat"])) * fraction
    lng = float(track[left]["lng"]) + (float(track[right]["lng"]) - float(track[left]["lng"])) * fraction
    return lat, lng


def event_timezone(event: dict[str, Any]) -> str | None:
    timezone = event.get("timezone")
    if not isinstance(timezone, str) or not timezone:
        return None
    try:
        ZoneInfo(timezone)
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError):
        return None
    return timezone


def event_start_utc(event: dict[str, Any], actual_only: bool = False) -> dt.datetime | None:
    date_text = str(event.get("event_date") or "")[:10]
    time_text = str(event.get("start_time") or (None if actual_only else event.get("estimated_start_time")) or "")
    if not date_text or not time_text:
        return None
    try:
        local_date = dt.date.fromisoformat(date_text)
        local_time = dt.time.fromisoformat(time_text)
        timezone = event_timezone(event)
        if timezone is None:
            return None
        zone = ZoneInfo(timezone)
        return dt.datetime.combine(local_date, local_time, zone).astimezone(dt.timezone.utc)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def split_progresses(
    event_response: dict[str, Any], course: dict[str, Any], track: list[dict[str, Any]], cumulative: list[float], total: float
) -> list[float | None]:
    """Unknown or ambiguous checkpoints stay unknown, never equally spaced."""
    event = event_response.get("event") or {}
    count = len(event.get("split_names") or [])
    values: list[float | None] = [None] * count
    if count < 2:
        return values
    for row in event_response.get("multipliers") or []:
        if not isinstance(row, dict):
            continue
        index = as_int(row.get("split_index"))
        progress = as_float(row.get("progress_pct"))
        if index is not None and 0 <= index < count and progress is not None and 0 <= progress <= 1:
            values[index] = progress

    values[0], values[-1] = 0.0, 1.0
    split_points = course.get("splitPoints") or []
    route = prepare_route(tuple((p["lat"], p["lng"]) for p in track))
    for index in range(1, count - 1):
        if values[index] is None and index < len(split_points) and isinstance(split_points[index], dict):
            location = coordinate(split_points[index].get("lat"), split_points[index].get("lng"))
            lower = next(value for value in reversed(values[:index]) if value is not None)
            upper = next(value for value in values[index + 1:] if value is not None)
            along, _ = match_route(location, route, lower, upper) if location else (None, None)
            values[index] = along / total if along is not None and total > 0 else None
    known = [value for value in values if value is not None]
    if any(right <= left for left, right in zip(known, known[1:])):
        return [0.0] + [None] * (count - 2) + [1.0]
    return values


def segment_weights(event_response: dict[str, Any], progresses: list[float]) -> list[float]:
    event = event_response.get("event") or {}
    distances = event.get("split_distances") or {}
    multipliers = {
        as_int(row.get("split_index")): as_float(row.get("pace_multiplier"))
        for row in event_response.get("multipliers") or []
        if isinstance(row, dict)
    }
    weights = [0.0]
    for index in range(1, len(progresses)):
        segment = distances.get(str(index)) or distances.get(index) or {}
        distance = as_float(segment.get("value"))
        if distance is None or distance <= 0:
            distance = max(0.001, progresses[index] - progresses[index - 1])
        multiplier = multipliers.get(index) or 1.0
        weights.append(max(0.001, distance * multiplier))
    return weights


def runner_status(runner: dict[str, Any], finish_index: int) -> str:
    explicit = str(runner.get("status") or "").upper()
    if explicit in {"FINISHED", "DNS", "DQ", "DNQ", "DNF", "DROPPED"}:
        return "DROPPED" if explicit == "DNF" else explicit
    if runner.get("dq"):
        return "DQ"
    if runner.get("dnq"):
        return "DNQ"
    if runner.get("dns"):
        return "DNS"
    if runner.get("dropped") or runner.get("dnf"):
        return "DROPPED"
    if runner.get("finish_time_seconds") is not None:
        return "FINISHED"
    if explicit == "REGISTERED":
        return "REGISTERED"
    last_index = as_int(runner.get("last_split_index"))
    if finish_index > 0 and last_index is not None and last_index >= finish_index:
        return "FINISHED"
    if last_index is not None and last_index >= 0:
        return "ON COURSE"
    return "REGISTERED"


def runner_start_utc(runner: dict[str, Any], start: dt.datetime | None) -> dt.datetime | None:
    """Chip elapsed uses event actual start plus the runner's chip offset.

    Only legacy in-memory callers may omit the key. Live ingestion explicitly
    inserts None for missing offsets; unknown is never evidence of a zero start.
    gun_start_seconds is a different clock and must not substitute for chip.
    """
    offset = as_float(runner.get("chip_start_seconds", 0))
    if offset is None or offset < 0:
        return None
    return after_seconds(start, offset)


def projected_progress(
    runner: dict[str, Any], event_response: dict[str, Any], progresses: list[float | None], now: dt.datetime
) -> tuple[float, float, bool] | None:
    event = event_response.get("event") or {}
    if len(progresses) < 2 or any(value is None for value in progresses):
        return None
    last_index = as_int(runner.get("last_split_index"))
    if last_index is None or last_index < 0 or last_index >= len(progresses) - 1:
        return None
    if runner_status(runner, len(progresses) - 1) != "ON COURSE":
        return None

    start = runner_start_utc(runner, event_start_utc(event, actual_only=True))
    if start is None:
        return None
    race_clock = (now.astimezone(dt.timezone.utc) - start).total_seconds()
    if race_clock < 0:
        return None

    last_time = as_float(runner.get("last_split_time"))
    if last_time is None or last_time < 0 or last_time > race_clock or (last_index > 0 and last_time == 0):
        return None
    base_progress = progresses[last_index]
    # Public leaderboard JS verifies last_split_time is chip-relative, but does
    # not establish estimated_finish_seconds' clock basis. Live chip records
    # therefore use observed checkpoint pace only, never ambiguous ETA/goal data.
    chip_timing = "chip_start_seconds" in runner
    projected_finish = None if chip_timing else as_float(runner.get("estimated_finish_seconds"))
    if projected_finish is None and last_time > 0 and base_progress > 0:
        projected_finish = last_time / base_progress
    if projected_finish is None and not chip_timing:
        projected_finish = as_float(runner.get("goal_time_seconds"))
    if projected_finish is None or projected_finish <= last_time or after_seconds(start, projected_finish) is None:
        return None

    travel_time = max(0.0, race_clock - last_time)
    remaining_time = projected_finish - last_time
    weights = segment_weights(event_response, progresses)
    if any(not math.isfinite(weight) or weight <= 0 for weight in weights[1:]):
        return None
    remaining_weights = weights[last_index + 1 :]
    total_weight = sum(remaining_weights)
    if not math.isfinite(total_weight):
        return None
    if total_weight <= 0:
        next_progress = progresses[last_index + 1]
        fraction = min(0.99, travel_time / remaining_time)
        return (
            base_progress + (next_progress - base_progress) * fraction,
            projected_finish,
            travel_time >= remaining_time,
        )

    # An estimate may move only toward the next checkpoint. If expected
    # arrival passes without a chip read, hold just before that checkpoint
    # instead of inventing movement through later course segments.
    next_index = last_index + 1
    next_duration = remaining_time * (weights[next_index] / total_weight)
    overdue = travel_time >= next_duration
    fraction = 0.99 if overdue else max(0.0, min(0.99, travel_time / max(1.0, next_duration)))
    progress = progresses[last_index] + (progresses[next_index] - progresses[last_index]) * fraction
    return progress, projected_finish, overdue


def checkpoint_forecasts(event_response: dict, progresses: list, index: int,
                         last_time: float, finish_seconds: float, terrain,
                         chip_start: dt.datetime, now: dt.datetime) -> list[dict[str, Any]]:
    """All-or-nothing future clocks, evaluated without changing the estimator.

    The fallback's finish duration is already fitted; distribute only remaining
    time by its exact segment weights, not by naive distance progress. Validate
    original checkpoint ordering before weights can conceal bad geometry.
    """
    if (not 0 < index < len(progresses) - 1
            or not all(as_float(p) is not None and 0 <= p <= 1 for p in progresses)
            or progresses[0] != 0 or progresses[-1] != 1
            or any(b <= a for a, b in zip(progresses, progresses[1:]))
            or as_float(last_time) is None or as_float(finish_seconds) is None
            or not 0 < last_time < finish_seconds):
        return []
    if terrain is not None:
        seconds = terrain.checkpoint_seconds
    else:
        weights = segment_weights(event_response, progresses)[index + 1:]
        total = sum(weights)
        if not math.isfinite(total) or total <= 0 or any(not math.isfinite(w) or w <= 0 for w in weights):
            return []
        seconds, cumulative = [], 0.0
        for i, weight in enumerate(weights, index + 1):
            cumulative += weight
            elapsed = finish_seconds if i == len(progresses) - 1 else last_time + (finish_seconds - last_time) * (cumulative / total)
            seconds.append((i, elapsed))
    if [i for i, _ in seconds] != list(range(index + 1, len(progresses))):
        return []
    output, previous = [], now
    for i, elapsed in seconds:
        arrival = after_seconds(chip_start, elapsed)
        if (arrival is None or arrival <= previous or elapsed <= last_time
                or (arrival - now).total_seconds() > MAX_CHECKPOINT_FORECAST_SECONDS):
            return []  # Never slide expired arrivals forward or publish a suffix.
        output.append({"split_index": i, "estimated_at": timestamp(arrival)})
        previous = arrival
    if output[-1]["estimated_at"] != timestamp(after_seconds(chip_start, finish_seconds)):
        return []
    return output


def protected_name(record: dict[str, Any]) -> str:
    if record.get("is_anonymous") or record.get("athlete_anonymous"):
        return "Anonymous Participant"
    return str(record.get("name") or record.get("runner_name") or "Unknown Runner")


def sanitize_runner(runner: dict[str, Any], finish_index: int) -> dict[str, Any]:
    anonymous = bool(runner.get("is_anonymous") or runner.get("athlete_anonymous"))
    return {
        "id": as_int(runner.get("id")),
        "bib": None if anonymous else runner.get("bib"),
        "name": protected_name(runner),
        "is_anonymous": anonymous,
        "city": "" if anonymous else runner.get("city") or "",
        "state": "" if anonymous else runner.get("state") or "",
        "age": None if anonymous else as_int(runner.get("age")),
        "gender": "" if anonymous else runner.get("gender") or "",
        "status": runner_status(runner, finish_index),
        "last_split_index": as_int(runner.get("last_split_index")),
        "last_split_time": as_float(runner.get("last_split_time")),
        **({"chip_start_seconds": as_float(runner.get("chip_start_seconds"))}
           if "chip_start_seconds" in runner else {}),
        "estimated_finish_seconds": as_float(runner.get("estimated_finish_seconds")),
        "goal_time_seconds": as_float(runner.get("goal_time_seconds")),
        "overall_place": None if anonymous else runner.get("gun_place") or runner.get("chip_place"),
        "has_gps": bool(runner.get("has_gps")),
        "checkpoint_passages": [],
        "checkpoint_passages_status": "unavailable",
        "checkpoint_passages_stale": False,
    }


def parse_recorded_at(value: Any) -> dt.datetime | None:
    return parse_timestamp(value)


def deduplicate_records(rows: list[dict[str, Any]], id_key: str) -> dict[int, dict[str, Any]]:
    """Keep one canonical id/runner_id observation and union strong evidence."""
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _participant_record_id(row)
        if key is None:
            continue
        previous = output.get(key)
        if previous is None:
            output[key] = dict(row)
            continue
        output[key] = _merge_participant_records(previous, row, id_key)
    return output


def _merge_participant_records(previous: dict[str, Any], row: dict[str, Any],
                               id_key: str) -> dict[str, Any]:
    """Merge one duplicate in constant work while preserving strong evidence."""
    anonymous = any(record.get(flag) for record in (previous, row)
                    for flag in ("is_anonymous", "athlete_anonymous"))
    if id_key == "runner_id":
        minimum = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        chosen = max((previous, row),
                     key=lambda record: parse_recorded_at(record.get("recorded_at")) or minimum)
    else:
        def evidence(record: dict[str, Any]) -> tuple:
            index = as_int(record.get("last_split_index"))
            return (runner_status(record, 0) not in {"ON COURSE", "REGISTERED"},
                    index if index is not None else -1,
                    as_float(record.get("last_split_time")) or 0)

        chosen = max((previous, row), key=evidence)
    merged = {**chosen, "is_anonymous": bool(anonymous)}
    for record in (previous, row):
        status = runner_status(record, 0)
        if status not in {"ON COURSE", "REGISTERED"}:
            merged["status"] = status
    return merged


def checkpoint_observation(clean: dict[str, Any], start: dt.datetime | None, now: dt.datetime) -> dt.datetime | None:
    index, elapsed = clean["last_split_index"], clean["last_split_time"]
    if index is None or index < 0 or elapsed is None or elapsed < 0 or (index > 0 and elapsed == 0):
        return None
    observed = after_seconds(runner_start_utc(clean, start), elapsed)
    return observed if observed is not None and observed <= now else None


@functools.lru_cache(maxsize=16)
def terrain_profile(cumulative: tuple, elevations: tuple):
    """Immutable course geometry is shared across runners and warm polls."""
    return build_profile(list(cumulative), list(elevations))


def validate_history_payload(payload: Any) -> str | None:
    """Validate legacy passage history, not a future complete cohort source.

    The legacy result cache exists only to enrich live checkpoint passages. It
    must not be repurposed as the complete cohort source for Results API work.
    """
    if not isinstance(payload, dict) or payload.get("status") not in (None, "ok"):
        return "history: malformed"
    rows = payload.get("results")
    if not isinstance(rows, list):
        return "history: malformed"
    if "has_more" in payload and type(payload["has_more"]) is not bool:
        return "history: malformed"
    ids = [_participant_record_id(row) if isinstance(row, dict) else None for row in rows]
    if None in ids or len(set(ids)) != len(ids):
        return "history: invalid or duplicate ids"
    if payload.get("has_more") is True:
        return "history: incomplete"
    for field in ("total", "total_count", "count"):
        if field in payload and as_int(payload[field]) != len(ids):
            return "history: conflicting total"
    return None


def checkpoint_history(record: dict | None, runner: dict, race_clock: float) -> tuple[list, str]:
    """Keep chip elapsed intact: split zero is an offset, not elapsed pace."""
    index, elapsed = as_int(runner.get("last_split_index")), as_float(runner.get("last_split_time"))
    fallback = [(0, 0.0), (index, elapsed)]
    if record is None:
        return fallback, "missing"
    offset = as_float(runner.get("chip_start_seconds"))
    history_offset = as_float(record.get("chip_start_seconds"))
    history_time = as_float(record.get("last_split_time"))
    if (_participant_record_id(record) != _participant_record_id(runner)
            or offset is None or history_offset is None or abs(offset - history_offset) > .01
            or as_int(record.get("last_split_index")) != index or history_time is None
            or elapsed is None or abs(history_time - elapsed) > .01):
        return fallback, "mismatch"
    splits = record.get("splits")
    if not isinstance(splits, list):
        return fallback, "malformed"
    output, previous = [(0, 0.0)], -1
    for split in splits:
        if not isinstance(split, dict):
            return fallback, "malformed"
        i, t = as_int(split.get("split_index")), as_float(split.get("elapsed_seconds"))
        if i is None or t is None or i <= previous or i < 0 or i > index or t < 0:
            return fallback, "malformed"
        previous = i
        if i == 0:
            if abs(t - offset) > .01:
                return fallback, "mismatch"
            continue
        if t <= output[-1][1] or t > race_clock or t > elapsed + .01:
            return fallback, "malformed"
        output.append((i, t))
    if output[-1][0] != index or abs(output[-1][1] - elapsed) > .01:
        return fallback, "mismatch"
    output[-1] = (index, elapsed)  # Leaderboard remains authoritative.
    if len(output) < 2 or output[-1][1] <= output[-2][1]:
        return fallback, "malformed"
    return output, "matched"


def checkpoint_passages(record: dict | None, runner: dict, split_count: int,
                        start: dt.datetime | None, now: dt.datetime,
                        history_status: str, timing_stale: bool = False) -> dict[str, Any]:
    """Public observations, not the estimator's virtual start/pace anchors.

    A recorded history is contiguous through the latest read, not necessarily
    through Finish. Bad/unavailable history retains only the leaderboard read;
    an absent Start split is never filled from an offset alone.
    """
    stale = bool(timing_stale or history_status == "stale")

    def result(rows: list, complete: bool = False) -> dict[str, Any]:
        return {"checkpoint_passages": rows,
                "checkpoint_passages_status": "recorded" if complete else "partial" if rows else "unavailable",
                "checkpoint_passages_stale": stale}

    index, elapsed = as_int(runner.get("last_split_index")), as_float(runner.get("last_split_time"))
    if (runner.get("status") in {"DNS", "REGISTERED"} or index is None or not 0 <= index < split_count
            or elapsed is None or elapsed < 0 or (index > 0 and elapsed == 0)
            or after_seconds(now, elapsed) is None or (start is not None and start > now)):
        return result([])
    # No legacy zero default here: offset absence is unknown in every caller.
    offset = as_float(runner.get("chip_start_seconds"))
    if offset is not None and offset < 0:
        offset = None
    chip_start = after_seconds(start, offset)
    if start is not None and offset is not None and (chip_start is None or chip_start > now):
        return result([])
    # Even an unknown nonnegative offset cannot make a future event-clock read
    # possible. With no actual clock, keep proven chip elapsed but no wall time.
    clock = (now - (chip_start or start)).total_seconds() if start is not None else None
    if index > 0 and clock is not None and elapsed > clock:
        return result([])

    def start_read(value: float) -> bool:
        return offset is not None and (value == 0 or abs(value - offset) <= .01)

    def row(i: int, seconds: float) -> dict[str, Any]:
        return {"split_index": i, "elapsed_seconds": seconds,
                "passed_at": timestamp(after_seconds(chip_start, seconds))}

    if index == 0 and (chip_start is None or not start_read(elapsed)):
        return result([])
    fallback = result([row(index, 0.0 if index == 0 else elapsed)])
    if history_status != "fresh" or timing_stale or not isinstance(record, dict):
        return fallback
    history_offset, history_time = as_float(record.get("chip_start_seconds")), as_float(record.get("last_split_time"))
    runner_id = as_int(runner.get("id"))
    if (runner_id is None or _participant_record_id(record) != runner_id
            or offset is None or history_offset is None or history_offset < 0 or abs(history_offset - offset) > .01
            or as_int(record.get("last_split_index")) != index or history_time is None or history_time < 0
            or abs(history_time - elapsed) > .01):
        return fallback
    splits = record.get("splits")
    if not isinstance(splits, list) or not splits:
        return fallback
    observed, previous_index, previous_time = [], -1, -1.0
    for split in splits:
        if not isinstance(split, dict):
            return fallback
        i, seconds = as_int(split.get("split_index")), as_float(split.get("elapsed_seconds"))
        if i is None or not previous_index < i <= index or seconds is None or seconds < 0:
            return fallback
        if i == 0:
            if not start_read(seconds):
                return fallback
            seconds = 0.0
        elif seconds <= previous_time or seconds <= 0 or seconds > elapsed + .01 or (clock is not None and seconds > clock):
            return fallback
        if i == index:
            latest = 0.0 if index == 0 else elapsed
            if abs(seconds - latest) > .01 or latest <= previous_time:
                return fallback
            seconds = latest  # Leaderboard remains authoritative.
        previous_index, previous_time = i, seconds
        if i > 0 or chip_start is not None:
            observed.append(row(i, seconds))
    if previous_index != index:
        return fallback
    return result(observed, complete=len(observed) == index + 1)


def build_event_view(bundle: dict[str, Any], now: dt.datetime, include_course: bool,
                     label_override: str | None = None, running_eligible: bool = True) -> dict[str, Any]:
    now = now.astimezone(dt.timezone.utc)
    event_response = bundle["event_response"]
    event = event_response.get("event") or {}
    timezone = event_timezone(event)
    label = label_override or EVENT_LABELS.get(
        event.get("id"), event.get("display_name") or event.get("name") or "Race"
    )
    raw_maps = (bundle.get("course_response") or {}).get("courseMaps")
    maps = raw_maps if isinstance(raw_maps, list) and len(raw_maps) <= MAX_COURSE_MAPS else []
    matching = [m for m in maps if isinstance(m, dict)
                and (m.get("eventId") or m.get("event_id")) == event.get("id")]
    # A single untagged legacy course is compatible; never guess among routes.
    if not matching and len(maps) == 1 and isinstance(maps[0], dict) and not (maps[0].get("eventId") or maps[0].get("event_id")):
        matching = maps
    course = matching[0] if len(matching) == 1 else {}
    raw_track = course.get("trackPoints")
    raw_splits = course.get("splitPoints")
    if (not isinstance(raw_track, list) or len(raw_track) > MAX_COURSE_TRACK_POINTS
            or (raw_splits is not None and (
                not isinstance(raw_splits, list) or len(raw_splits) > MAX_COURSE_SPLIT_POINTS))):
        # Never publish a connected prefix of an oversized or malformed route.
        course = {}
    track = []
    for point in course.get("trackPoints") or []:
        location = coordinate(point.get("lat"), point.get("lng")) if isinstance(point, dict) else None
        if location is None:
            track = []  # Do not silently connect a gap across an invalid route point.
            break
        elevation = as_float(point.get("ele"))
        track.append({"lat": location[0], "lng": location[1], **({"ele": elevation} if elevation is not None else {})})
    route = prepare_route(tuple((p["lat"], p["lng"]) for p in track))
    cumulative, total = list(route.cumulative), route.total
    progresses = split_progresses(event_response, course, track, cumulative, total)
    profile = terrain_profile(tuple(cumulative), tuple(p.get("ele") for p in track)) if TERRAIN_PILOT_ENABLED else None
    history_status = bundle.get("history_status", "not_loaded")
    history_fetched_at = bundle.get("history_fetched_at")
    history_error = bundle.get("history_error")
    if history_status == "fresh":
        fetched = parse_timestamp(history_fetched_at)
        if fetched is None or not 0 <= (now - fetched).total_seconds() < HISTORY_TTL_SECONDS:
            history_status, history_error = "stale", "history: stale"
    history_by_id = deduplicate_records(bundle.get("history") or [], "id")
    # Do not let deduplication turn conflicting bulk rows into public history.
    # Keep estimator behavior and privacy/terminal evidence unions unchanged.
    passages_history_status = history_status
    if history_status == "fresh" and (history_error or validate_history_payload({"results": bundle.get("history") or []})):
        passages_history_status = "unavailable"
    finish_index = max(0, len(progresses) - 1)
    split_names = list(event.get("split_names") or [])
    start = event_start_utc(event, actual_only=True)
    degraded = bool(bundle.get("upstream_stale") or bundle.get("errors"))
    source_freshness = {name: dict(state) for name, state in (bundle.get("source_freshness") or {}).items()}
    for state in source_freshness.values():
        fetched = parse_timestamp(state.get("fetched_at"))
        ttl = as_float(state.get("ttl_seconds"))
        age = (now - fetched).total_seconds() if fetched else None
        # Cache refresh TTLs stay unchanged; allow only bounded assembly latency.
        if age is None or age < 0 or ttl is None or ttl <= 0 or age >= ttl + SOURCE_ASSEMBLY_GRACE_SECONDS:
            state["stale"] = True
    degraded = degraded or any(s.get("stale") or s.get("error") for s in source_freshness.values())
    timing_stale = any(source_freshness.get(name, {}).get("stale") or source_freshness.get(name, {}).get("error")
                       for name in ("event", "leaderboard")) if source_freshness else degraded

    leaderboard = deduplicate_records(bundle.get("leaderboard") or [], "id")
    # Mixed/late-wave in-memory bundles also cannot treat a missing runner
    # offset as zero merely because a legacy caller bypassed live ingestion.
    if any("chip_start_seconds" in runner for runner in leaderboard.values()):
        leaderboard = {key: {**runner, "chip_start_seconds": runner.get("chip_start_seconds")}
                       for key, runner in leaderboard.items()}
    gps_by_id = deduplicate_records((bundle.get("gps_response") or {}).get("positions") or [], "runner_id")
    runners, positions = [], []
    for runner_id in dict.fromkeys([*leaderboard, *gps_by_id]):
        gps = gps_by_id.get(runner_id)
        raw = leaderboard.get(runner_id) or {"name": (gps or {}).get("runner_name"),
                                             "bib": (gps or {}).get("bib"), "has_gps": True}
        raw = {**raw, "id": runner_id}
        evidence = (raw, gps or {}, history_by_id.get(runner_id) or {})
        anonymous = any(record.get(flag) for record in evidence
                        for flag in ("is_anonymous", "athlete_anonymous"))
        raw = {**raw, "is_anonymous": bool(anonymous)}
        for record in evidence:
            status = runner_status(record, finish_index)
            if status not in {"ON COURSE", "REGISTERED"}:
                raw["status"] = status
        clean = sanitize_runner({**raw, "is_anonymous": bool(anonymous)}, finish_index)
        clean.update(event_id=event.get("id"), course=label, has_gps=gps is not None or clean["has_gps"])
        if runner_id not in leaderboard and clean["status"] in {"ON COURSE", "REGISTERED"}:
            clean["status"] = "GPS"
        clean.update(checkpoint_passages(history_by_id.get(runner_id), clean, len(split_names),
                                         start, now, passages_history_status, timing_stale))
        runners.append(clean)
        index = clean["last_split_index"]
        observation = checkpoint_observation(clean, start, now)
        interval = None
        if index is not None and 0 <= index < len(progresses) - 1:
            lower, upper = progresses[index:index + 2]
            if lower is not None and upper is not None and 0 <= lower < upper <= 1:
                interval = (lower, upper)

        reason = None
        if clean["status"] != "ON COURSE":
            reason = "NOT_ON_COURSE"
        elif start is None or start > now:
            reason = "EVENT_NOT_STARTED"
        elif str(event.get("course_status") or "active").lower() not in {"active", "started", "in_progress"}:
            reason = "EVENT_NOT_ACTIVE"
        elif observation is None:
            reason = "INVALID_CHECKPOINT_TIME"
        elif degraded:
            reason = "UPSTREAM_STALE"
        elif total <= 0:
            reason = "ROUTE_UNAVAILABLE"
        elif interval is None:
            reason = "CHECKPOINT_INTERVAL_UNAVAILABLE"

        # On failure return the last observed checkpoint, not time-driven motion.
        projection = None
        terrain = None
        row_history_status = history_status
        chip_start = runner_start_utc(clean, start) if "chip_start_seconds" in clean else None
        if (profile is not None and chip_start is not None and observation is not None
                and clean["status"] == "ON COURSE" and index is not None and index > 0 and interval):
            clock = ((observation if degraded else now) - chip_start).total_seconds()
            history = [(0, 0.0), (index, clean["last_split_time"])]
            if history_status == "fresh":
                history, row_history_status = checkpoint_history(history_by_id.get(runner_id), clean, clock)
            terrain = predict(profile, progresses, history, index, clean["last_split_time"], clock)
            if terrain is not None and after_seconds(chip_start, terrain.finish_seconds) is not None:
                projection = (progresses[index] if degraded else terrain.progress, terrain.finish_seconds, terrain.overdue)
            else:
                terrain = None
        if projection is None:
            projection = projected_progress(raw, event_response, progresses, observation if degraded and observation else now)
        row = {**clean, "remaining_m": None, "distance_source": None, "rank_eligible": False,
               "rank_exclusion": reason, "eta_at": None, "eta_basis": None, "observation_at": None,
               "checkpoint_forecasts": [], "checkpoint_forecast_basis": None,
               "progress": None, "last_checkpoint": split_names[index] if index is not None and 0 <= index < len(split_names) else None}
        if terrain is not None:
            row.update(estimate_model=TERRAIN_MODEL, estimate_basis="TERRAIN_CHECKPOINT_PILOT",
                       pace_basis=terrain.pace_basis, pace_segments_used=terrain.pace_segments_used,
                       pace_window_seconds=terrain.pace_window_seconds, checkpoint_history_status=row_history_status)
        if gps is not None:
            location = coordinate(gps.get("latitude"), gps.get("longitude"))
            if location is None:
                continue  # GPS precedence: an invalid fix is never replaced with an estimate.
            recorded = parse_recorded_at(gps.get("recorded_at"))
            age = (now - recorded).total_seconds() if recorded else None
            fresh = age is not None and 0 <= age <= FRESH_GPS_SECONDS and not degraded
            accuracy = as_float(gps.get("accuracy"))
            along, match_error = match_route(location, route, *interval, accuracy_m=accuracy) if interval and total > 0 else (None, "ROUTE_UNAVAILABLE")
            if along is not None:
                row.update(progress=along / total, remaining_m=max(0.0, total - along), distance_source="GPS_MATCHED")
            row.update(lat=location[0], lng=location[1], source="GPS", freshness="LIVE" if fresh else "STALE",
                       recorded_at=timestamp(recorded), observation_at=timestamp(recorded),
                       age_seconds=round(age) if age is not None else None,
                       accuracy_m=accuracy, speed_mps=as_float(gps.get("speed")),
                       heading=as_float(gps.get("heading")), battery_pct=as_float(gps.get("battery_pct")))
            if not reason:
                if recorded is None:
                    reason = "INVALID_GPS_TIME"
                elif age < 0:
                    reason = "FUTURE_GPS_TIME"
                elif age > FRESH_GPS_SECONDS:
                    reason = "STALE_GPS"
                elif observation and recorded < observation:
                    reason = "GPS_BEFORE_CHECKPOINT"
                elif gps.get("accuracy") is not None and accuracy is None:
                    reason = "GPS_INACCURATE"
                else:
                    reason = match_error
        else:
            if projection is None or total <= 0:
                continue
            progress, duration, overdue = projection
            location = interpolate_route(track, cumulative, total, progress)
            if location is None:
                continue
            capped = interval is not None and progress >= interval[0] + (interval[1] - interval[0]) * .99
            held = overdue or degraded or capped
            row.update(lat=location[0], lng=location[1], source="ESTIMATED", freshness="STALE" if degraded else "ESTIMATED",
                       recorded_at=None, observation_at=timestamp(observation),
                       age_seconds=round((now - observation).total_seconds()) if observation else None,
                       accuracy_m=None, speed_mps=None, heading=None, battery_pct=None,
                       progress=progress, remaining_m=max(0.0, total * (1 - progress)), distance_source="SPLIT_ESTIMATE",
                       projected_finish_seconds=duration, estimate_overdue=overdue, estimate_held=held,
                       estimate_basis="TERRAIN_CHECKPOINT_PILOT" if terrain else
                       "CHECKPOINT_PACE_CHIP" if "chip_start_seconds" in clean else "LEGACY_EVENT_CLOCK")
            if not reason and overdue:
                reason = "ESTIMATE_OVERDUE"
            if not reason and held:
                reason = "ESTIMATE_HELD"
            if not reason and (index is None or index == 0):
                reason = "UNSUPPORTED_PROJECTION"

        row.update(rank_eligible=reason is None, rank_exclusion=reason)
        # Timing must be supported by a non-start checkpoint, not a goal alone.
        if reason is None and projection and not projection[2] and index is not None and index > 0:
            eta = after_seconds(runner_start_utc(clean, start), projection[1])
            if eta is not None and eta > now:
                row["eta_at"] = timestamp(eta)
                row["eta_basis"] = "TERRAIN_CHECKPOINT_PILOT" if terrain else "CHECKPOINT_PACE_CHIP" if "chip_start_seconds" in clean else "LEGACY_EVENT_CLOCK"
                if terrain is not None and not row.get("estimate_held"):
                    next_at = after_seconds(chip_start, terrain.next_checkpoint_seconds)
                    if next_at is not None and next_at > now:
                        row["next_checkpoint_at"] = timestamp(next_at)
                if chip_start is not None and not row.get("estimate_held"):
                    forecasts = checkpoint_forecasts(event_response, progresses, index,
                        clean["last_split_time"], projection[1], terrain, chip_start, now)
                    if forecasts:
                        row.update(checkpoint_forecasts=forecasts, checkpoint_forecast_basis=row["eta_basis"],
                                   next_checkpoint_at=forecasts[0]["estimated_at"])
        positions.append(row)

    has_results = history_status in {"fresh", "stale"} and bool(history_by_id)
    has_course = len(track) >= 2 and total > 0
    has_elevation = has_course and all("ele" in point for point in track)
    has_gps = any(row.get("source") == "GPS" for row in positions)
    view: dict[str, Any] = {
        "id": event.get("id"), "label": label, "display_name": event.get("display_name") or label,
        "event_date": str(event.get("event_date") or "")[:10] or None,
        "start_time": event.get("start_time") or event.get("estimated_start_time"),
        "start_at": timestamp(start), "timezone": timezone,
        "course_status": event.get("course_status") or "unknown",
        "distance_miles": as_float(event.get("course_distance_miles")), "route_length_m": total if total > 0 else None,
        "split_names": split_names, "color": course.get("color") or "#ff6b35",
        "runners": runners, "positions": positions, "upstream_stale": degraded,
        "source_freshness": source_freshness, "errors": bundle.get("errors") or [],
        "capabilities": {
            "results": has_results,
            "intermediate_splits": len(split_names) > 2,
            "course_map": has_course,
            "elevation": has_elevation,
            "live_tracking": event.get("live_tracking_enabled") is True or has_gps,
            "gps": has_gps,
            "running_eligible": bool(running_eligible),
        },
        "estimator": {"model": TERRAIN_MODEL, "terrain_ready": profile is not None,
                      "enabled": TERRAIN_PILOT_ENABLED, "history_status": history_status,
                      "history_fetched_at": history_fetched_at, "history_error": history_error,
                      "historical_baseline": "not_applied",
                      **({"elevation_gain_m": profile.gain_m, "elevation_loss_m": profile.loss_m} if profile else {})},
    }
    if include_course:
        view["course"] = {
            "track_points": track,
            "split_points": [{"lat": p["lat"], "lng": p["lng"], "name": str(p.get("name") or "")}
                             for p in course.get("splitPoints") or []
                             if isinstance(p, dict) and coordinate(p.get("lat"), p.get("lng")) is not None],
            "progress_points": progresses,
        }
    return view


def _finalize_leaderboard(rows: Any) -> list[dict[str, Any]]:
    """Re-apply the allowlist after cross-page privacy evidence is merged."""
    return [_sanitize_participant_record(row, _LEADERBOARD_FIELDS, "id")
            for row in rows if isinstance(row, dict)]


def _leaderboard_cache_key(event_id: str) -> str:
    identifier = catalog.validate_identifier(event_id)
    return f"dynamic-leaderboard:{identifier}"


def _load_leaderboard_pages(
        event_id: str,
        fetch_page: Callable[[str], tuple[dict[str, Any], bool]],
        *,
        clock: Callable[[], float] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch pages with bounded work and incremental canonical identity merging."""
    output: dict[int, dict[str, Any]] = {}
    offset, response_rows, stale = 0, 0, False
    totals: set[int] = set()
    clock = clock or time.monotonic

    def read_clock() -> float:
        try:
            value = clock()
        except Exception:
            raise RuntimeError("leaderboard clock unavailable") from None
        if (type(value) not in (int, float) or isinstance(value, bool)
                or not math.isfinite(value)):
            raise RuntimeError("leaderboard clock unavailable")
        return float(value)

    def incomplete() -> tuple[list[dict[str, Any]], bool]:
        return _finalize_leaderboard(output.values()), True

    started_at = read_clock()
    last_clock = started_at
    for _page in range(MAX_LEADERBOARD_PAGES):
        before_request = read_clock()
        if (before_request < last_clock
                or before_request - started_at > MAX_LEADERBOARD_LOAD_SECONDS
                or response_rows >= MAX_LEADERBOARD_RESPONSE_ROWS
                or len(output) >= MAX_LEADERBOARD_UNIQUE_RESULTS):
            return incomplete()
        last_clock = before_request
        try:
            path = (f"/events/{urllib.parse.quote(event_id, safe='')}/leaderboard"
                    f"?limit={LEADERBOARD_PAGE_SIZE}&offset={offset}")
            payload, was_stale = fetch_page(path)
        except Exception:
            if not output:
                raise
            return incomplete()
        after_request = read_clock()
        if (after_request < last_clock
                or after_request - started_at > MAX_LEADERBOARD_LOAD_SECONDS):
            return incomplete()
        last_clock = after_request
        stale = stale or bool(was_stale)
        batch = payload.get("leaderboard") if isinstance(payload, dict) else None
        if not isinstance(batch, list) or len(batch) > LEADERBOARD_PAGE_SIZE:
            if not output:
                raise RuntimeError("leaderboard was not a bounded list")
            return incomplete()
        if "has_more" in payload and type(payload["has_more"]) is not bool:
            return incomplete()
        if not _leaderboard_page_count_is_valid(payload, batch):
            return incomplete()
        if response_rows + len(batch) > MAX_LEADERBOARD_RESPONSE_ROWS:
            return incomplete()

        staged: list[tuple[int, dict[str, Any]]] = []
        for row in batch:
            if not isinstance(row, dict):
                return incomplete()
            identity = _participant_record_id(row)
            if identity is None:
                return incomplete()
            staged.append((identity, row))
        new_identities = {identity for identity, _row in staged}.difference(output)
        if len(output) + len(new_identities) > MAX_LEADERBOARD_UNIQUE_RESULTS:
            return incomplete()

        before_count = len(output)
        for identity, row in staged:
            previous = output.get(identity)
            output[identity] = (dict(row) if previous is None
                                else _merge_participant_records(previous, row, "id"))
        response_rows += len(batch)
        offset += len(batch)

        after_processing = read_clock()
        if (after_processing < last_clock
                or after_processing - started_at > MAX_LEADERBOARD_LOAD_SECONDS):
            return incomplete()
        last_clock = after_processing

        for field in ("total", "total_count"):
            if field in payload:
                total = as_int(payload[field])
                if total is None or not 0 <= total <= MAX_LEADERBOARD_UNIQUE_RESULTS:
                    return incomplete()
                totals.add(total)
        more = payload.get("has_more")
        count = len(output)
        # Totals remain hard assertions across pages, including zero and aliases.
        stale = stale or len(totals) > 1 or any(count > total for total in totals)
        if more is True:
            stale = stale or any(count >= total for total in totals)
        elif more is False or (totals and count >= max(totals)):
            stale = stale or any(count != total for total in totals)
            break
        if not batch or len(output) == before_count:
            stale = stale or bool(batch) or more is True or any(count != total for total in totals)
            break
        if len(batch) < LEADERBOARD_PAGE_SIZE and more is not True and not totals:
            break
    else:
        stale = True  # A page-capped response is incomplete.
    return _finalize_leaderboard(output.values()), stale


def load_leaderboard(event_id: str, *,
                     max_stale_seconds: float | None = None) -> tuple[list[dict[str, Any]], bool]:
    event_id = catalog.validate_identifier(event_id)
    if max_stale_seconds is None:
        return _load_leaderboard_pages(
            event_id,
            lambda path: upstream_json(path, LEADERBOARD_TTL_SECONDS),
        )

    # Dynamic pages stay request-local. Only the event-wide, post-union value
    # enters the shared cache, so a later page can invalidate earlier identity.
    def load_merged() -> list[dict[str, Any]]:
        rows, incomplete = _load_leaderboard_pages(
            event_id,
            lambda path: (_fetch_sanitized_upstream(path), False),
        )
        if incomplete:
            # Never replace an event-wide privacy union with a partial refresh:
            # an unfetched later page may contain stronger anonymity evidence.
            raise RuntimeError("leaderboard incomplete")
        return rows

    value, cache_stale = CACHE.get(
        _leaderboard_cache_key(event_id), LEADERBOARD_TTL_SECONDS, load_merged,
        max_stale_seconds=max_stale_seconds,
    )
    return value, cache_stale


def load_checkpoint_history(event_id: str, *,
                            max_stale_seconds: float | None = None) -> dict[str, Any]:
    event_id = catalog.validate_identifier(event_id)
    # Recorded passage display is independent of the optional terrain model.
    history = []
    path = f"/events/{urllib.parse.quote(event_id, safe='')}/results"
    try:
        policy = ({"max_stale_seconds": max_stale_seconds}
                  if max_stale_seconds is not None else {})
        payload, stale = upstream_json(path, HISTORY_TTL_SECONDS, **policy)
        # Preserve privacy/terminal evidence even if timing is unusable.
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            history = [row for row in payload["results"] if isinstance(row, dict)]
        error = validate_history_payload(payload)
        status = "unavailable" if error else "stale" if stale else "fresh"
        if stale and not error:
            error = "history: stale"
    except Exception:
        status, error = "unavailable", "history: unavailable"
    fetched_at = CACHE.source_fetched_at(UPSTREAM + path)
    return {"history": history, "history_status": status,
            "history_error": error, "history_fetched_at": fetched_at}


def load_event_bundle(event_id: str, history_bundle: dict | None = None, *,
                      dynamic: bool = False) -> dict[str, Any]:
    event_id = catalog.validate_identifier(event_id)
    errors: list[str] = []
    source_freshness: dict[str, dict[str, Any]] = {}
    quoted = urllib.parse.quote(event_id, safe="")

    def fetch(source: str, path: str, ttl: int, fallback: Any) -> Any:
        try:
            maximum = {
                "event": DYNAMIC_EVENT_MAX_STALE_SECONDS,
                "course": DYNAMIC_COURSE_MAX_STALE_SECONDS,
                "gps": DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
                "leaderboard": DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS,
            }[source] if dynamic else None
            policy = {"max_stale_seconds": maximum} if maximum is not None else {}
            value, stale = (load_leaderboard(event_id, **policy) if source == "leaderboard"
                            else upstream_json(path, ttl, **policy))
            # Both cached fallback and partial pagination need a public signal,
            # but never include arbitrary upstream exceptions or response bodies.
            error = f"{source}: stale or incomplete" if stale else None
            if error:
                errors.append(error)
            cache_key = (_leaderboard_cache_key(event_id)
                         if dynamic and source == "leaderboard" else UPSTREAM + path)
            source_freshness[source] = {"fetched_at": CACHE.source_fetched_at(cache_key),
                                        "stale": bool(stale), "error": error, "ttl_seconds": ttl}
            return value
        except Exception:
            # Never relay arbitrary upstream text or participant payloads as errors.
            error = f"{source}: unavailable"
            errors.append(error)
            cache_key = (_leaderboard_cache_key(event_id)
                         if dynamic and source == "leaderboard" else UPSTREAM + path)
            source_freshness[source] = {"fetched_at": CACHE.source_fetched_at(cache_key),
                                        "stale": True, "error": error, "ttl_seconds": ttl}
            return fallback

    if history_bundle is None:
        history_bundle = load_checkpoint_history(
            event_id,
            **({"max_stale_seconds": DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS} if dynamic else {}),
        )
    # Finish optional work before all core freshness-sensitive fetches.
    # Legacy Rut views keep their historical metadata fallback. Dynamic event
    # selection must prove identity from an actual sanitized event response.
    event_fallback = {} if dynamic else {"event": {"id": event_id}}
    event_response = fetch("event", f"/events/{quoted}", EVENT_TTL_SECONDS, event_fallback)
    course_response = fetch("course", f"/course-maps/event/{quoted}?include=points", COURSE_TTL_SECONDS, {})
    gps_response = fetch("gps", f"/gps/locations/{quoted}", GPS_TTL_SECONDS, {})
    leaderboard = fetch("leaderboard", f"/events/{quoted}/leaderboard?limit=500&offset=0", LEADERBOARD_TTL_SECONDS, [])
    # The upstream UI defaults missing chip offsets to zero for display only;
    # that cannot prove a late-wave runner's wall-clock start. Preserve unknown
    # explicitly at ingestion so every live projection fails closed.
    leaderboard = [{**runner, "chip_start_seconds": runner.get("chip_start_seconds")}
                   for runner in leaderboard]
    return {
        "event_response": event_response, "course_response": course_response,
        "gps_response": gps_response, "leaderboard": leaderboard,
        "upstream_stale": any(s["stale"] for s in source_freshness.values()),
        "source_freshness": source_freshness, "errors": errors,
        **history_bundle,
    }


PUBLIC_DROP_FIELDS = {"age", "gender", "city", "state", "battery_pct", "speed_mps", "heading"}


def minimize_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy and strip fields that the public selected-event contract does not need."""
    clean = copy.deepcopy(payload)
    for event in clean.get("events") or []:
        for collection in ("runners", "positions"):
            for record in event.get(collection) or []:
                for field in PUBLIC_DROP_FIELDS:
                    record.pop(field, None)
    return clean


def _payload_for_views(views: list[dict[str, Any]], now: dt.datetime) -> dict[str, Any]:
    positions = [position for event in views for position in event["positions"]]
    live_gps = sum(1 for row in positions if row["source"] == "GPS" and row["freshness"] == "LIVE")
    stale_gps = sum(1 for row in positions if row["source"] == "GPS" and row["freshness"] == "STALE")
    estimated = sum(1 for row in positions if row["source"] == "ESTIMATED")
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "poll_after_seconds": GPS_TTL_SECONDS,
        "summary": {
            "live_gps": live_gps,
            "stale_gps": stale_gps,
            "estimated": estimated,
            "positions": len(positions),
            "upstream_stale": any(event["upstream_stale"] for event in views),
            "errors": sum(len(event["errors"]) for event in views),
        },
        "events": views,
    }


def build_catalog_payload(query: str, year: Any = None, limit: Any = None,
                          catalog_cache: catalog.CatalogCache | None = None) -> dict[str, Any]:
    """Return only bounded search matches from the cached minimized catalog."""
    catalog.validate_search_parameters(query, year=year, limit=limit)
    cache = catalog_cache or CATALOG
    rows = cache.get()
    return {"status": "ok", "stale": getattr(cache, "stale", False) is True,
            "races": catalog.search_catalog(rows, query, year=year, limit=limit)}


def build_selected_event(event_id: str, include_course: bool = False,
                         now: dt.datetime | None = None,
                         catalog_cache: catalog.CatalogCache | None = None) -> dict[str, Any]:
    """Build the existing public payload shape for one allowlisted catalog event."""
    identifier = catalog.validate_identifier(event_id)
    races = (catalog_cache or CATALOG).get()
    _, event_summary = catalog.find_event(races, identifier)
    requested_now = now
    history = load_checkpoint_history(
        identifier, max_stale_seconds=DYNAMIC_PARTICIPANT_MAX_STALE_SECONDS
    )
    bundle = load_event_bundle(identifier, history, dynamic=True)
    source_event = (bundle.get("event_response") or {}).get("event")
    if not isinstance(source_event, dict) or source_event.get("id") != identifier:
        raise SelectedEventUnavailable("selected event unavailable")
    now = (requested_now or utc_now()).astimezone(dt.timezone.utc)
    label = event_summary.get("label") or event_summary.get("name") or "Race"
    view = build_event_view(bundle, now, include_course, label_override=str(label))
    return minimize_public_payload(_payload_for_views([view], now))


def build_payload(include_courses: bool = False, now: dt.datetime | None = None) -> dict[str, Any]:
    requested_now = now
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(EVENTS)) as executor:
        # Complete optional history for EVERY event before fetching any core
        # snapshot. Otherwise one race's slow history can age another's GPS.
        history_futures = {event_id: executor.submit(load_checkpoint_history, event_id) for event_id, _ in EVENTS}
        histories = {event_id: future.result() for event_id, future in history_futures.items()}
        futures = {event_id: executor.submit(load_event_bundle, event_id, histories[event_id]) for event_id, _ in EVENTS}
        bundles = [(event_id, futures[event_id].result()) for event_id, _ in EVENTS]
    now = (requested_now or utc_now()).astimezone(dt.timezone.utc)
    views = [build_event_view(bundle, now, include_courses) for _, bundle in bundles]
    return _payload_for_views(views, now)


def _query_fields(query: dict[str, list[str]], allowed: set[str]) -> dict[str, str]:
    if set(query) - allowed or any(not isinstance(values, list) or len(values) != 1 for values in query.values()):
        raise catalog.CatalogQueryError("invalid parameters")
    return {key: values[0] for key, values in query.items()}


def catalog_request_parameters(query: dict[str, list[str]]) -> tuple[str, str | None, str | None]:
    fields = _query_fields(query, {"q", "year", "limit"})
    if "q" not in fields:
        raise catalog.CatalogQueryError("invalid query")
    catalog.validate_search_parameters(fields["q"], year=fields.get("year"), limit=fields.get("limit"))
    return fields["q"], fields.get("year"), fields.get("limit")


def event_request_parameters(query: dict[str, list[str]]) -> tuple[str, bool]:
    fields = _query_fields(query, {"event_id", "include_course"})
    if "event_id" not in fields:
        raise catalog.InvalidIdentifier("invalid identifier")
    event_id = catalog.validate_identifier(fields["event_id"])
    include = fields.get("include_course", "0")
    if include not in {"0", "1"}:
        raise catalog.CatalogQueryError("invalid include_course")
    return event_id, include == "1"


class ResultsRequestError(ValueError):
    """A malformed Results request; its details are never returned publicly."""


class ResultsNotFound(LookupError):
    """A fixed event or fresh completed runner was not found."""


class ResultsUnavailable(RuntimeError):
    """A required Results source is unavailable or too stale to prove a miss."""


def _results_query_fields(query: Any, allowed: frozenset[str]) -> dict[str, str]:
    if not isinstance(query, dict) or set(query) - allowed:
        raise ResultsRequestError("invalid parameters")
    fields: dict[str, str] = {}
    for key, values in query.items():
        if type(values) is not list or len(values) != 1 or type(values[0]) is not str:
            raise ResultsRequestError("invalid parameters")
        fields[key] = values[0]
    return fields


def _require_fixed_results_event(value: Any) -> str:
    if (type(value) is not str or not value or len(value) > 200
            or _RESULTS_EVENT_PATTERN.fullmatch(value) is None):
        raise ResultsRequestError("invalid event")
    if value not in RESULTS_EVENT_IDS:
        raise ResultsNotFound("unsupported event")
    return value


def _canonical_results_decimal(value: Any, maximum: int) -> int:
    if (type(value) is not str or not value or not value.isascii() or not value.isdigit()
            or value[0] == "0"):
        raise ResultsRequestError("invalid decimal")
    maximum_text = str(maximum)
    if len(value) > len(maximum_text) or (
            len(value) == len(maximum_text) and value > maximum_text):
        raise ResultsRequestError("invalid decimal")
    return int(value)


def results_search_request_parameters(
        query: dict[str, list[str]],
) -> tuple[str, str, int]:
    """Build the strict Rut-event search request shared by both adapters."""
    fields = _results_query_fields(query, frozenset({"event_id", "q", "limit"}))
    if "event_id" not in fields or "q" not in fields:
        raise ResultsRequestError("missing parameters")
    event_id = _require_fixed_results_event(fields["event_id"])
    normalized = " ".join(fields["q"].split())
    if (not 2 <= len(normalized) <= 100
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized)):
        raise ResultsRequestError("invalid query")
    limit_text = fields.get("limit")
    limit = (MAX_RESULTS_SEARCH_LIMIT if limit_text is None
             else _canonical_results_decimal(limit_text, MAX_RESULTS_SEARCH_LIMIT))
    return event_id, normalized, limit


def results_runner_request_parameters(
        query: dict[str, list[str]],
) -> tuple[str, int]:
    """Build the strict Rut-event runner request shared by both adapters."""
    fields = _results_query_fields(query, frozenset({"event_id", "runner_id"}))
    if "event_id" not in fields or "runner_id" not in fields:
        raise ResultsRequestError("missing parameters")
    event_id = _require_fixed_results_event(fields["event_id"])
    return event_id, _canonical_results_decimal(fields["runner_id"], MAX_RESULTS_RUNNER_ID)


def parse_results_query(query_string: Any, *, route_parameter: str | None = None) -> dict[str, list[str]]:
    """Strictly decode one bounded Results query, optionally removing a rewrite route."""
    if type(query_string) is not str or len(query_string) > 2048:
        raise ResultsRequestError("invalid query string")
    for index, character in enumerate(query_string):
        if character == "%" and (
                index + 2 >= len(query_string)
                or any(digit not in "0123456789abcdefABCDEF" for digit in query_string[index + 1:index + 3])):
            raise ResultsRequestError("invalid percent escape")
    try:
        query = urllib.parse.parse_qs(
            query_string,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=4 if route_parameter is not None else 3,
        )
    except (UnicodeError, ValueError):
        raise ResultsRequestError("invalid query string") from None
    if route_parameter is not None:
        routes = query.pop("route", None)
        if routes != [route_parameter]:
            raise ResultsRequestError("invalid route parameter")
    return query


def _results_public_text(value: Any, fallback: str, maximum: int = 200) -> str:
    if type(value) is not str:
        return fallback
    text = " ".join(value.split())
    if not text or any(ord(character) < 32 or ord(character) == 127 for character in text):
        return fallback
    return text[:maximum]


def _results_event_source(
        raw_response: Any, event_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (not isinstance(raw_response, dict)
            or raw_response.get("status") not in (None, "ok")):
        raise ResultsUnavailable("event unavailable")
    try:
        response = sanitize_event_payload(raw_response)
    except Exception:
        raise ResultsUnavailable("event unavailable") from None
    source_event = response.get("event")
    if not isinstance(source_event, dict) or source_event.get("id") != event_id:
        raise ResultsUnavailable("event unavailable")
    raw_names = source_event.get("split_names")
    if not isinstance(raw_names, list) or not 2 <= len(raw_names) <= MAX_RESULT_SPLITS:
        raise ResultsUnavailable("event unavailable")
    names = [_results_public_text(name, "") for name in raw_names]
    if any(not name for name in names):
        raise ResultsUnavailable("event unavailable")
    analysis_event = {
        "event": {"id": event_id, "split_names": names},
        "multipliers": response.get("multipliers") if isinstance(response.get("multipliers"), list) else [],
    }
    return analysis_event, source_event


def _results_exact_course(raw_response: Any, event_id: str) -> dict[str, Any] | None:
    if (not isinstance(raw_response, dict)
            or raw_response.get("status") not in (None, "ok")):
        return None
    try:
        maps = sanitize_course_payload(raw_response).get("courseMaps")
    except Exception:
        return None
    if not isinstance(maps, list) or len(maps) != 1 or not isinstance(maps[0], dict):
        return None
    course = maps[0]
    aliases = [course[field] for field in ("event_id", "eventId") if field in course]
    if not aliases or any(type(value) is not str or value != event_id for value in aliases):
        return None
    return course


def _valid_results_progresses(values: Any, count: int) -> list[float] | None:
    if not isinstance(values, list) or len(values) != count:
        return None
    progresses = []
    for value in values:
        if type(value) not in (int, float) or isinstance(value, bool):
            return None
        number = float(value)
        if not math.isfinite(number) or not 0 <= number <= 1:
            return None
        progresses.append(number)
    if (not progresses or progresses[0] != 0 or progresses[-1] != 1
            or any(right <= left for left, right in zip(progresses, progresses[1:]))):
        return None
    return progresses


def _results_course_geometry(
        event_response: dict[str, Any], course: dict[str, Any] | None, event_id: str,
) -> tuple[dict[str, Any] | None, list[float] | None, dict[str, Any] | None, bool]:
    """Return analysis/public geometry only from one bounded exact-event map."""
    if course is None:
        return None, None, None, False
    raw_track = course.get("trackPoints")
    if (not isinstance(raw_track, list) or not 2 <= len(raw_track) <= MAX_COURSE_TRACK_POINTS):
        return None, None, None, False
    track = []
    all_elevations = True
    for raw_point in raw_track:
        if not isinstance(raw_point, dict):
            return None, None, None, False
        location = coordinate(raw_point.get("lat"), raw_point.get("lng"))
        if location is None:
            return None, None, None, False
        point: dict[str, float] = {"lat": location[0], "lng": location[1]}
        elevation = finite_number(raw_point.get("ele"))
        if elevation is None or not -12_000 <= elevation <= 12_000:
            all_elevations = False
        else:
            point["ele"] = elevation
        track.append(point)
    route = prepare_route(tuple((point["lat"], point["lng"]) for point in track))
    if not math.isfinite(route.total) or route.total <= 0:
        return None, None, None, False
    try:
        derived = split_progresses(
            event_response, course, track, list(route.cumulative), route.total,
        )
    except Exception:
        derived = None
    split_count = len(event_response["event"]["split_names"])
    progresses = _valid_results_progresses(derived, split_count)
    analysis_course = {"eventId": event_id, "trackPoints": track}

    if len(track) <= 2_000:
        public_track = track
    else:
        public_track = [
            track[round(index * (len(track) - 1) / (2_000 - 1))]
            for index in range(2_000)
        ]
    public_course: dict[str, Any] = {"track_points": copy.deepcopy(public_track)}
    if progresses is not None:
        public_course["progress_points"] = list(progresses)
    return analysis_course, progresses, public_course, all_elevations


def build_results_search_payload(
        event_id: str, query: str, limit: int, *,
        completed_cache: CompletedResultsCache | None = None,
) -> dict[str, Any]:
    """Search only the selected Rut event's sanitized completed-results cache."""
    _require_fixed_results_event(event_id)
    cache = completed_cache or RESULTS_CACHE
    completed = cache.get(event_id)
    stale = getattr(cache, "stale", False) is True
    matches = search_public_runners(completed, query, limit)
    return {"status": "ok", "event_id": event_id, "stale": stale, "matches": matches}


def build_results_runner_payload(
        event_id: str, runner_id: int, *,
        completed_cache: CompletedResultsCache | None = None,
        upstream_loader: Callable[..., tuple[dict[str, Any], bool]] | None = None,
) -> dict[str, Any]:
    """Analyze one Rut-event runner without loading catalog, live, GPS, or history data."""
    _require_fixed_results_event(event_id)
    cache = completed_cache or RESULTS_CACHE
    loader = upstream_loader or upstream_json
    completed = cache.get(event_id)
    completed_stale = getattr(cache, "stale", False) is True

    raw_event, event_stale = loader(
        f"/events/{event_id}", EVENT_TTL_SECONDS,
        max_stale_seconds=DYNAMIC_EVENT_MAX_STALE_SECONDS,
    )
    if type(event_stale) is not bool:
        raise ResultsUnavailable("event unavailable")
    analysis_event, public_event_source = _results_event_source(raw_event, event_id)

    course = None
    course_stale = False
    try:
        raw_course, loaded_course_stale = loader(
            f"/course-maps/event/{event_id}?include=points", COURSE_TTL_SECONDS,
            max_stale_seconds=DYNAMIC_COURSE_MAX_STALE_SECONDS,
        )
        if type(loaded_course_stale) is bool:
            course = _results_exact_course(raw_course, event_id)
            course_stale = loaded_course_stale and course is not None
    except Exception:
        course = None
    analysis_course, progresses, public_course, elevation_available = _results_course_geometry(
        analysis_event, course, event_id,
    )
    try:
        analysis = analyze_runner(
            analysis_event, analysis_course, progresses, completed, runner_id,
        )
    except LookupError:
        if completed_stale:
            raise ResultsUnavailable("stale completed miss") from None
        raise ResultsNotFound("runner not found") from None

    analysis_capabilities = analysis.get("capabilities") if isinstance(analysis, dict) else None
    if not isinstance(analysis_capabilities, dict):
        raise ResultsUnavailable("analysis unavailable")
    event_capabilities = {
        "results": True,
        "intermediate_splits": bool(
            len(analysis_event["event"]["split_names"]) > 2
            and analysis_capabilities.get("splits") is True
        ),
        "course_map": public_course is not None,
        "elevation": bool(public_course is not None and elevation_available),
        "live_gps": False,
    }
    date_value = public_event_source.get("event_date")
    event_date = None
    if type(date_value) is str and len(date_value) >= 10:
        try:
            event_date = dt.date.fromisoformat(date_value[:10]).isoformat()
        except ValueError:
            event_date = None
    event_payload: dict[str, Any] = {
        "id": event_id,
        "label": EVENT_LABELS[event_id],
        "event_date": event_date,
        "capabilities": event_capabilities,
    }
    if public_course is not None:
        event_payload["course"] = public_course
    return {
        "status": "ok",
        "stale": bool(completed_stale or event_stale or course_stale),
        "race": {"slug": RESULTS_RACE_SLUG, "name": RESULTS_RACE_NAME},
        "event": event_payload,
        "analysis": analysis,
    }


def results_response(
        route: str, query: dict[str, list[str]], *,
        completed_cache: CompletedResultsCache | None = None,
        upstream_loader: Callable[..., tuple[dict[str, Any], bool]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return one shared, generic-error Results response for either adapter."""
    if route not in RESULTS_ROUTES:
        return 404, {"status": "error", "message": "Not found"}
    try:
        if route == "results/search":
            event_id, search_query, limit = results_search_request_parameters(query)
            return 200, build_results_search_payload(
                event_id, search_query, limit, completed_cache=completed_cache,
            )
        event_id, runner_id = results_runner_request_parameters(query)
        return 200, build_results_runner_payload(
            event_id, runner_id, completed_cache=completed_cache,
            upstream_loader=upstream_loader,
        )
    except ResultsRequestError:
        return 400, {"status": "error", "message": "Invalid results request"}
    except ResultsNotFound:
        return 404, {"status": "error", "message": "Results not found"}
    except Exception:
        return 503, {"status": "error", "message": "Results unavailable; retry shortly"}


def _valid_static_application_route(query_string: str) -> bool:
    """Accept only the privacy-safe Rut application routes served by index.html."""
    try:
        query = urllib.parse.parse_qs(
            query_string,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=4,
        )
    except (UnicodeError, ValueError):
        return False
    if set(query) - {"race", "event", "mode", "runner"}:
        return False
    if any(type(values) is not list or len(values) != 1 or type(values[0]) is not str
           for values in query.values()):
        return False
    if query.get("race") != [RESULTS_RACE_SLUG]:
        return False
    event_values = query.get("event")
    if event_values is None or event_values[0] not in RESULTS_EVENT_IDS:
        return False
    mode_values = query.get("mode")
    if mode_values is None or mode_values[0] not in {"live", "results"}:
        return False
    runner_values = query.get("runner")
    if runner_values is None:
        return True
    if mode_values != ["results"]:
        return False
    try:
        _canonical_results_decimal(runner_values[0], MAX_RESULTS_RUNNER_ID)
    except ResultsRequestError:
        return False
    return True


def _static_relative_path(request_target: str) -> str | None:
    """Return one canonical allowlisted path, never a normalized alias."""
    if (not isinstance(request_target, str)
            or any(ord(character) < 32 or ord(character) == 127 for character in request_target)
            or "\\" in request_target or "%" in request_target or "#" in request_target):
        return None
    try:
        parsed = urllib.parse.urlsplit(request_target)
    except ValueError:
        return None
    path = parsed.path
    if (parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith("/")
            or "//" in path or any(part in {".", ".."} for part in path.split("/"))):
        return None

    if "?" in request_target:
        if not parsed.query:
            return None
        if path == "/":
            if not _valid_static_application_route(parsed.query):
                return None
        else:
            try:
                query = urllib.parse.parse_qs(
                    parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=1,
                )
            except ValueError:
                return None
            versions = query.get("v")
            if (set(query) != {"v"} or not isinstance(versions, list) or len(versions) != 1
                    or not versions[0] or len(versions[0]) > 32 or not versions[0].isascii()
                    or any(not (character.isalnum() or character in ".-_") for character in versions[0])):
                return None

    relative = "index.html" if path == "/" else path[1:]
    return relative if relative in STATIC_FILES else None


def _read_regular_static_file(relative: str) -> bytes | None:
    """Read one in-root file through no-follow directory/file descriptors.

    The descriptor walk closes the validation/use race inherent in resolving a
    pathname and reading it later. A link count other than one is rejected so
    an allowlisted name cannot be a hardlink alias for an undeclared file.
    """
    parts = relative.split("/")
    if (not parts or any(not part or part in {".", ".."} for part in parts)
            or "\\" in relative):
        return None
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_fd = None
    file_fd = None
    try:
        directory_fd = os.open(APP_ROOT, directory_flags | close_on_exec)
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            return None
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags | close_on_exec, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                return None

        file_fd = os.open(parts[-1], file_flags | close_on_exec, dir_fd=directory_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            return None
        with os.fdopen(file_fd, "rb", closefd=False) as source:
            body = source.read()
        after = os.fstat(file_fd)
        before_identity = (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns, before.st_nlink,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns, after.st_nlink,
        )
        if (after_identity != before_identity or after.st_nlink != 1
                or len(body) != after.st_size):
            return None
        return body
    except (OSError, RuntimeError, ValueError):
        return None
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                pass


class RutHandler(BaseHTTPRequestHandler):
    server_version = "RutLiveMap/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        """Keep all Results query names and values out of local access logs."""
        try:
            path = urllib.parse.urlsplit(self.path).path
        except ValueError:
            path = ""
        if path in RESULTS_PATHS:
            self.log_message(
                '"%s %s %s" %s %s',
                self.command, path, self.request_version, str(code), str(size),
            )
            return
        super().log_request(code, size)

    def send_payload(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in RESULTS_PATHS:
                route = parsed.path.removeprefix("/api/")
                try:
                    query = parse_results_query(parsed.query)
                except ResultsRequestError:
                    response = 400, {"status": "error", "message": "Invalid results request"}
                else:
                    response = results_response(route, query)
                self.send_payload(*response)
                return
            if parsed.path == "/api/health":
                self.send_payload(200, {"status": "ok", "app": APP_NAME, "version": APP_VERSION})
                return
            if parsed.path == "/api/bootstrap":
                self.send_payload(200, build_payload(include_courses=True))
                return
            if parsed.path == "/api/live":
                self.send_payload(200, build_payload(include_courses=False))
                return
            if parsed.path == "/api/catalog":
                try:
                    query, year, limit = catalog_request_parameters(
                        urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                    )
                    self.send_payload(200, build_catalog_payload(query, year=year, limit=limit))
                except catalog.CatalogQueryError:
                    self.send_payload(400, {"status": "error", "message": "Invalid catalog query"})
                except Exception:
                    self.send_payload(503, {"status": "error", "message": "Race catalog unavailable; retry shortly"})
                return
            if parsed.path == "/api/event":
                try:
                    event_id, include_course = event_request_parameters(
                        urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                    )
                    self.send_payload(200, build_selected_event(event_id, include_course=include_course))
                except (catalog.InvalidIdentifier, catalog.CatalogQueryError):
                    self.send_payload(400, {"status": "error", "message": "Invalid event request"})
                except catalog.UnknownEvent:
                    self.send_payload(404, {"status": "error", "message": "Event not found"})
                except Exception:
                    self.send_payload(503, {"status": "error", "message": "Event data unavailable; retry shortly"})
                return
            if parsed.path.startswith("/api/"):
                self.send_payload(404, {"status": "error", "message": "not found"})
                return
            request_parts = self.requestline.split()
            request_target = request_parts[1] if len(request_parts) >= 2 else self.path
            self.serve_static(request_target)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self.send_payload(502, {"status": "error", "message": "request failed"})

    def serve_static(self, request_target: str) -> None:
        relative = _static_relative_path(request_target)
        if relative is None:
            self.send_error(404)
            return
        body = _read_regular_static_file(relative)
        if body is None:
            self.send_error(404)
            return
        content_type = ("text/markdown" if Path(relative).suffix.lower() == ".md"
                        else mimetypes.guess_type(relative)[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https://*.tile.openstreetmap.org https://*.tile.opentopomap.org; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self';")
        self.end_headers()
        self.wfile.write(body)


def own_server_running(port: int) -> bool:
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=1) as response:
            payload = json.load(response)
        return payload.get("app") == APP_NAME
    except Exception:
        return False


def serve(port: int, open_browser: bool) -> int:
    url = f"http://127.0.0.1:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), RutHandler)
    except OSError as exc:
        if own_server_running(port):
            if open_browser:
                webbrowser.open(url)
            print(f"{APP_NAME} is already running at {url}")
            return 0
        print(f"Could not start {APP_NAME} on port {port}: {exc}", file=sys.stderr)
        return 1

    print(f"{APP_NAME} {APP_VERSION} — {url}")
    print("Press Control-C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("RUT_LIVE_PORT", "8765")))
    parser.add_argument("--open", action="store_true", help="open the app in the default browser")
    parser.add_argument("--snapshot", action="store_true", help="print one live snapshot and exit")
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(build_payload(include_courses=False), indent=2))
        return 0
    return serve(args.port, args.open)


if __name__ == "__main__":
    raise SystemExit(main())
