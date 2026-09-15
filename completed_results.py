"""Sanitized source and bounded stale cache for completed race results.

The stable public interface is deliberately small:

* :func:`sanitize_results_payload` minimizes one ``/events/{id}/results`` body.
* :func:`sanitize_leaderboard_payload` keeps only identity/anonymity evidence.
* :class:`CompletedResultsCache` loads both sources and returns a deep-copied
  ``{"results": [...], "leaderboard": [...]}`` mapping from :meth:`get`.

Event-specific network paths are always derived from an identifier accepted by
:func:`competitive_catalog.validate_identifier` and the fixed Competitive
Timing API host. Raw payloads are loader-local only. The cache stores no raw
bodies, writes nothing to disk, and never includes upstream exception text in
its public failure.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Mapping
from numbers import Real
from typing import Any

from competitive_catalog import validate_identifier

__all__ = [
    "CompletedResultsCache",
    "CompletedResultsUnavailable",
    "fetch_leaderboard_payload",
    "fetch_results_payload",
    "sanitize_leaderboard_payload",
    "sanitize_results_payload",
]

API_HOST = "https://api.competitivetiming.com"
# Eight MiB bounds decoded JSON input without pretending stdlib json is a
# streaming parser. Structural checks below also apply to injected loaders.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RESULT_ROWS = 100_000
MAX_SPLIT_READINGS = 100
# At roughly a few hundred bytes per minimized split dict, this keeps a single
# sanitized value in the tens-of-MiB range instead of permitting 10M dicts.
MAX_TOTAL_SPLIT_READINGS = 100_000
MAX_SOURCE_TEXT_CHARS = 1024
MAX_CACHED_EVENTS = 8
DEFAULT_TTL_SECONDS = 15 * 60
LEADERBOARD_PAGE_SIZE = 500
MAX_LEADERBOARD_PAGES = (MAX_RESULT_ROWS + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE
REQUEST_TIMEOUT_SECONDS = 30
MAX_LEADERBOARD_LOAD_SECONDS = 90
MAX_TIME_SECONDS = 1e12
MAX_NAME_LENGTH = 200
MAX_BIB_LENGTH = 64
MAX_DEMOGRAPHIC_LENGTH = 64
MAX_PUBLIC_AGE = 120
MAX_RESULT_IDENTIFIER = 9_007_199_254_740_991
MAX_NUMERIC_BIB = 9_007_199_254_740_991
MAX_SPLIT_INDEX = MAX_SPLIT_READINGS - 1
MAX_PLACE = MAX_RESULT_ROWS
MAX_DECLARED_TOTAL = MAX_RESULT_ROWS
MAX_DECLARED_COUNT = MAX_RESULT_ROWS
MAX_DECLARED_PAGE_COUNT = LEADERBOARD_PAGE_SIZE

_ANONYMITY_FIELDS = ("is_anonymous", "athlete_anonymous")
_TERMINAL_BOOLEAN_FIELDS = ("dns", "dq", "dnq", "dnf", "dropped")
_RESULT_PLACE_FIELDS = ("finish_place", "overall_place", "place")
_DEMOGRAPHIC_PLACE_FIELDS = (
    "chip_age_group_place", "gun_age_group_place",
    "chip_gender_place", "gun_gender_place",
)
_SPLIT_PLACE_FIELDS = (
    "cumulative_place", "overall_place", "place", "rank",
    "segment_place", "split_place", "interval_place", "segment_rank",
)
_KNOWN_STATUSES = frozenset({
    "FINISH", "FINISHED", "FINISHER", "COMPLETE", "COMPLETED",
    "DNF", "DNS", "DQ", "DNQ", "DROPPED", "DID_NOT_FINISH",
    "DID_NOT_START", "DISQUALIFIED", "INCOMPLETE",
})
_GENERIC_ERROR = "completed results unavailable"


class CompletedResultsUnavailable(RuntimeError):
    """A generic source/cache failure that never contains upstream details."""


def _unavailable() -> CompletedResultsUnavailable:
    return CompletedResultsUnavailable(_GENERIC_ERROR)


def _clean_text(value: Any, maximum: int) -> str | None:
    if type(value) is not str or len(value) > MAX_SOURCE_TEXT_CHARS:
        return None
    without_controls = "".join(char if ord(char) >= 32 and ord(char) != 127 else " "
                               for char in value)
    clean = " ".join(without_controls.split())
    return clean[:maximum] if clean else None


def _canonical_bounded_decimal(value: Any, maximum: int) -> str | None:
    """Bound decimal text lexically, without an unsafe ``int`` conversion."""
    if (type(value) is not str or not value
            or len(value) > MAX_SOURCE_TEXT_CHARS
            or not value.isascii() or not value.isdigit()):
        return None
    canonical = value.lstrip("0") or "0"
    maximum_text = str(maximum)
    if (len(canonical) > len(maximum_text)
            or (len(canonical) == len(maximum_text) and canonical > maximum_text)):
        return None
    return canonical


def _bounded_nonnegative_integer(value: Any, maximum: int) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def _bounded_positive_integer(value: Any, maximum: int) -> int | None:
    return value if type(value) is int and 0 < value <= maximum else None


def _source_identifier(value: Any) -> tuple[str, int | str] | None:
    integer = _bounded_nonnegative_integer(value, MAX_RESULT_IDENTIFIER)
    if integer is not None:
        # Compare the bound before str(): hostile giant integers can exceed
        # Python's integer-to-decimal conversion limit.
        return str(integer), integer
    canonical = _canonical_bounded_decimal(value, MAX_RESULT_IDENTIFIER)
    if canonical is not None:
        # Numeric strings and integers use the same value bound and canonical
        # identity, including zero-padded source strings.
        return canonical, canonical
    return None


def _row_identifier(row: Mapping[str, Any]) -> tuple[str, str, int | str] | None:
    found = []
    for field in ("id", "runner_id"):
        if field not in row or row.get(field) is None:
            continue
        parsed = _source_identifier(row.get(field))
        if parsed is None:
            return None
        found.append((field, parsed[0], parsed[1]))
    if not found or any(item[1] != found[0][1] for item in found[1:]):
        return None
    field, canonical, value = found[0]
    return canonical, field, value


def _finite_time(value: Any) -> float | None:
    # JSON numbers arrive as exact int/float objects. Reject subclasses and
    # giant integers before invoking conversion hooks or allocating decimal
    # representations for attacker-controlled injected values.
    if type(value) is int:
        if not 0 <= value <= MAX_TIME_SECONDS:
            return None
        return float(value)
    if type(value) is not float:
        return None
    if not math.isfinite(value) or not 0 <= value <= MAX_TIME_SECONDS:
        return None
    return value


def _nonnegative_index(value: Any) -> int | None:
    return _bounded_nonnegative_integer(value, MAX_SPLIT_INDEX)


def _positive_place(value: Any) -> int | None:
    return _bounded_positive_integer(value, MAX_PLACE)


def _demographic_place(value: Any) -> int | None:
    """Accept the provider's canonical decimal placement strings only here."""
    if type(value) is int:
        return _positive_place(value)
    if (type(value) is not str or not value or len(value) > len(str(MAX_PLACE))
            or not value.isascii() or not value.isdigit()
            or (len(value) > 1 and value.startswith("0"))):
        return None
    return _positive_place(int(value))


def _public_bib(value: Any) -> int | str | None:
    integer = _bounded_nonnegative_integer(value, MAX_NUMERIC_BIB)
    if integer is not None:
        return integer
    if (type(value) is str and len(value) <= MAX_SOURCE_TEXT_CHARS
            and value.isascii() and value.isdigit()):
        return _canonical_bounded_decimal(value, MAX_NUMERIC_BIB)
    return _clean_text(value, MAX_BIB_LENGTH)


def _status(value: Any) -> str | None:
    text = _clean_text(value, 40)
    if text is None:
        return None
    normalized = text.upper().replace(" ", "_")
    return normalized if normalized in _KNOWN_STATUSES else None


def _source_flag(value: Any, *, privacy_fail_closed: bool = False) -> bool | None:
    """Normalize JSON booleans/0/1 without retaining malformed flag values."""
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if value is None:
        return None
    return True if privacy_fail_closed else None


def _validate_raw_rows(rows: list[Any], field: str) -> None:
    """Apply O(1) list caps, then a bounded structural results pre-pass."""
    if len(rows) > MAX_RESULT_ROWS:
        raise _unavailable()
    if field != "results":
        return
    split_total = 0
    for raw in rows:
        if not isinstance(raw, Mapping) or "splits" not in raw:
            continue
        splits = raw.get("splits")
        if type(splits) is not list:
            continue
        split_count = len(splits)
        if split_count > MAX_SPLIT_READINGS:
            raise _unavailable()
        split_total += split_count
        if split_total > MAX_TOTAL_SPLIT_READINGS:
            raise _unavailable()


def _payload_rows(payload: Any, field: str, *, allow_list: bool = False) -> list[Any]:
    if allow_list and type(payload) is list:
        rows = payload
        metadata = None
    else:
        if not isinstance(payload, Mapping) or payload.get("status") not in (None, "ok"):
            raise _unavailable()
        rows = payload.get(field)
        if type(rows) is not list:
            raise _unavailable()
        metadata = payload
    _validate_raw_rows(rows, field)
    if metadata is None:
        return rows
    if "has_more" in metadata:
        has_more = metadata.get("has_more")
        if type(has_more) is not bool or has_more:
            raise _unavailable()
    for count_field in ("total", "total_count", "count"):
        if count_field not in metadata:
            continue
        count = metadata.get(count_field)
        maximum = MAX_DECLARED_COUNT if count_field == "count" else MAX_DECLARED_TOTAL
        if (_bounded_nonnegative_integer(count, maximum) is None
                or count != len(rows)):
            raise _unavailable()
    return rows


def _strict_has_more(payload: Mapping[str, Any]) -> bool | None:
    if "has_more" not in payload:
        return None
    value = payload.get("has_more")
    if type(value) is not bool:
        raise _unavailable()
    return value


def _event_identity(row: Mapping[str, Any], expected: str | None) -> tuple[bool, str | None]:
    values = []
    for field in ("event_id", "eventId"):
        if field not in row or row.get(field) is None:
            continue
        raw_value = row.get(field)
        if type(raw_value) is not str or len(raw_value) > MAX_SOURCE_TEXT_CHARS:
            return False, None
        try:
            values.append(validate_identifier(raw_value))
        except Exception:
            return False, None
    if values and any(value != values[0] for value in values[1:]):
        return False, None
    source = values[0] if values else None
    if source is not None and expected is not None and source != expected:
        return False, None
    return True, source


def _sanitize_splits(value: Any) -> list[dict[str, Any]]:
    if type(value) is not list:
        return []
    if len(value) > MAX_SPLIT_READINGS:
        raise _unavailable()
    output = []
    seen = set()
    for raw in value:
        if len(output) >= MAX_SPLIT_READINGS:
            break
        if not isinstance(raw, Mapping):
            return []
        index = _nonnegative_index(raw.get("split_index"))
        elapsed = _finite_time(raw.get("elapsed_seconds"))
        if index is None or elapsed is None:
            return []
        if index in seen:
            continue
        seen.add(index)
        split = {"split_index": index, "elapsed_seconds": elapsed}
        for field in _SPLIT_PLACE_FIELDS:
            if field in raw:
                place = _positive_place(raw.get(field))
                if place is not None:
                    split[field] = place
        output.append(split)
    return output


def _sanitize_result_row(raw: Any, expected_event_id: str | None) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(raw, Mapping):
        return None
    identity = _row_identifier(raw)
    if identity is None:
        return None
    canonical, identity_field, identity_value = identity
    event_ok, source_event_id = _event_identity(raw, expected_event_id)
    if not event_ok:
        return None

    row: dict[str, Any] = {identity_field: identity_value}
    if source_event_id is not None:
        row["event_id"] = source_event_id

    for field in _ANONYMITY_FIELDS:
        if field in raw:
            value = _source_flag(raw.get(field), privacy_fail_closed=True)
            if value is not None:
                row[field] = value
    for field in _TERMINAL_BOOLEAN_FIELDS:
        if field in raw:
            value = _source_flag(raw.get(field))
            if value is not None:
                row[field] = value

    anonymous = any(row.get(field) is True for field in _ANONYMITY_FIELDS)
    if not anonymous:
        name = _clean_text(raw.get("name"), MAX_NAME_LENGTH)
        bib = _public_bib(raw.get("bib"))
        if name is not None:
            row["name"] = name
        if bib is not None:
            row["bib"] = bib
        age = _positive_place(raw.get("age"))
        gender = _clean_text(raw.get("gender"), MAX_DEMOGRAPHIC_LENGTH)
        age_group = _clean_text(raw.get("age_group"), MAX_DEMOGRAPHIC_LENGTH)
        if age is not None and age <= MAX_PUBLIC_AGE:
            row["age"] = age
        if gender is not None:
            row["gender"] = gender
        if age_group is not None:
            row["age_group"] = age_group
        for field in _DEMOGRAPHIC_PLACE_FIELDS:
            if field in raw:
                place = _demographic_place(raw.get(field))
                if place is not None:
                    row[field] = place

    status = _status(raw.get("status"))
    if status is not None:
        row["status"] = status

    timing_invalid = False
    if "chip_start_seconds" in raw and raw.get("chip_start_seconds") is not None:
        chip_start = _finite_time(raw.get("chip_start_seconds"))
        if chip_start is not None:
            row["chip_start_seconds"] = chip_start

    for field in ("last_split_time", "finish_time_seconds"):
        if field not in raw or raw.get(field) is None:
            continue
        value = _finite_time(raw.get(field))
        if value is None:
            timing_invalid = True
        else:
            row[field] = value

    if "last_split_index" in raw and raw.get("last_split_index") is not None:
        last_index = _nonnegative_index(raw.get("last_split_index"))
        if last_index is None:
            timing_invalid = True
        else:
            row["last_split_index"] = last_index

    for field in _RESULT_PLACE_FIELDS:
        if field in raw:
            place = _positive_place(raw.get(field))
            if place is not None:
                row[field] = place

    if "splits" in raw:
        row["splits"] = _sanitize_splits(raw.get("splits"))
    if timing_invalid:
        # Preserve public search identity/status but make timing unusable rather
        # than silently repairing malformed official summaries by omission.
        row["splits"] = []
        row.pop("last_split_index", None)
        row.pop("last_split_time", None)
        row.pop("finish_time_seconds", None)
    return canonical, row


def _merge_anonymity(target: dict[str, Any], evidence: Mapping[str, Any]) -> None:
    for field in _ANONYMITY_FIELDS:
        if field not in evidence:
            continue
        value = _source_flag(evidence.get(field), privacy_fail_closed=True)
        if value is not None and (field not in target or value is True):
            target[field] = value
    if any(target.get(field) is True for field in _ANONYMITY_FIELDS):
        for field in ("name", "bib", "age", "gender", "age_group", *_DEMOGRAPHIC_PLACE_FIELDS):
            target.pop(field, None)


def sanitize_results_payload(payload: Any, *, event_id: Any = None) -> list[dict[str, Any]]:
    """Return a new capped allowlist of analysis-compatible result records.

    ``event_id``, when supplied, is validated and event-mismatched records are
    omitted. Duplicate runner identities retain the first row, while anonymity
    flags are unioned across every matching duplicate before the value returns.
    """
    expected = validate_identifier(event_id) if event_id is not None else None
    raw_rows = _payload_rows(payload, "results")
    output: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for raw in raw_rows:
        sanitized = _sanitize_result_row(raw, expected)
        if sanitized is None:
            continue
        canonical, row = sanitized
        position = positions.get(canonical)
        if position is not None:
            _merge_anonymity(output[position], row)
            continue
        if len(output) >= MAX_RESULT_ROWS:
            continue
        positions[canonical] = len(output)
        output.append(row)
    return output


def sanitize_leaderboard_payload(payload: Any) -> list[dict[str, Any]]:
    """Return capped runner-ID/anonymity evidence from a leaderboard body."""
    raw_rows = _payload_rows(payload, "leaderboard", allow_list=True)
    output: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        identity = _row_identifier(raw)
        if identity is None:
            continue
        canonical, identity_field, identity_value = identity
        evidence: dict[str, Any] = {identity_field: identity_value}
        _merge_anonymity(evidence, raw)
        position = positions.get(canonical)
        if position is not None:
            _merge_anonymity(output[position], evidence)
            continue
        if len(output) >= MAX_RESULT_ROWS:
            continue
        positions[canonical] = len(output)
        output.append(evidence)
    return output


def _is_fixed_api_origin(url: Any) -> bool:
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
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )


class _FixedHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only while they remain on the fixed HTTPS API origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_fixed_api_origin(newurl):
            raise _unavailable()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_fixed_host(request: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(_FixedHostRedirectHandler())
    return opener.open(request, timeout=timeout)


def _read_fixed_host_json(
    path: str,
    byte_limit: int,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], int]:
    request = urllib.request.Request(API_HOST + path, headers={
        "Accept": "application/json",
        "Origin": "https://competitivetiming.com",
        "Referer": "https://competitivetiming.com/",
        "User-Agent": "CourseSignalCompletedResults/1.0 (+read-only public results)",
    })
    failed = False
    result: tuple[dict[str, Any], int] | None = None
    try:
        if (type(timeout) not in (int, float) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or timeout <= 0
                or timeout > REQUEST_TIMEOUT_SECONDS):
            raise _unavailable()
        with _open_fixed_host(request, timeout=timeout) as response:
            # Revalidate the final origin in addition to the pre-fetch redirect
            # policy, before reading or accepting any response bytes.
            if not _is_fixed_api_origin(response.geturl()):
                raise _unavailable()
            if getattr(response, "status", None) != 200:
                raise _unavailable()
            body = response.read(byte_limit + 1)
        if len(body) > byte_limit:
            raise _unavailable()
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise _unavailable()
        result = (payload, len(body))
    except Exception:
        # Raise after leaving the handler so the public exception does not keep
        # an inspectable raw upstream exception in __context__.
        failed = True
    if failed or result is None:
        raise _unavailable()
    return result


def _quoted_event_id(event_id: Any) -> str:
    identifier = validate_identifier(event_id)
    return urllib.parse.quote(identifier, safe="")


def fetch_results_payload(event_id: Any) -> dict[str, Any]:
    """Fetch one bounded results body from the fixed Competitive Timing host."""
    quoted = _quoted_event_id(event_id)
    payload, _size = _read_fixed_host_json(f"/events/{quoted}/results", MAX_RESPONSE_BYTES)
    # Stdlib json is not streaming: pair the byte cap with a post-decode shape
    # and aggregate pre-pass before returning the raw loader-local payload.
    _payload_rows(payload, "results")
    return payload


def _declared_total(payload: Mapping[str, Any]) -> int | None:
    totals = []
    for field in ("total", "total_count"):
        if field not in payload:
            continue
        value = payload.get(field)
        if _bounded_nonnegative_integer(value, MAX_DECLARED_TOTAL) is None:
            raise _unavailable()
        totals.append(value)
    if totals and any(value != totals[0] for value in totals[1:]):
        raise _unavailable()
    return totals[0] if totals else None


def _validate_page_count(payload: Mapping[str, Any], actual: int) -> None:
    if "count" not in payload:
        return
    count = payload.get("count")
    if (_bounded_nonnegative_integer(count, MAX_DECLARED_PAGE_COUNT) is None
            or count != actual):
        raise _unavailable()


def _network_clock() -> float:
    failed = False
    try:
        value = time.monotonic()
    except Exception:
        failed = True
        value = None
    if failed:
        raise _unavailable()
    number = _finite_time(value)
    if number is None:
        raise _unavailable()
    return number


def fetch_leaderboard_payload(event_id: Any) -> dict[str, Any]:
    """Fetch a complete bounded leaderboard using fixed limit/offset pages."""
    quoted = _quoted_event_id(event_id)
    records: list[Any] = []
    identities: set[str] = set()
    remaining_bytes = MAX_RESPONSE_BYTES
    declared_total = None
    completed = False
    started_at = _network_clock()
    last_clock = started_at
    for _page in range(MAX_LEADERBOARD_PAGES):
        offset = len(records)
        if remaining_bytes <= 0:
            raise _unavailable()
        before_request = _network_clock()
        if before_request < last_clock:
            raise _unavailable()
        elapsed = before_request - started_at
        remaining_seconds = MAX_LEADERBOARD_LOAD_SECONDS - elapsed
        if remaining_seconds <= 0:
            raise _unavailable()
        last_clock = before_request
        payload, used = _read_fixed_host_json(
            f"/events/{quoted}/leaderboard?limit={LEADERBOARD_PAGE_SIZE}&offset={offset}",
            remaining_bytes,
            timeout=min(float(REQUEST_TIMEOUT_SECONDS), remaining_seconds),
        )
        after_request = _network_clock()
        if (after_request < last_clock
                or after_request - started_at > MAX_LEADERBOARD_LOAD_SECONDS):
            raise _unavailable()
        last_clock = after_request
        if type(used) is not int or not 0 <= used <= remaining_bytes:
            raise _unavailable()
        remaining_bytes -= used
        if payload.get("status") not in (None, "ok"):
            raise _unavailable()
        batch = payload.get("leaderboard")
        if type(batch) is not list:
            raise _unavailable()
        if len(batch) > LEADERBOARD_PAGE_SIZE:
            raise _unavailable()
        _validate_page_count(payload, len(batch))
        page_total = _declared_total(payload)
        if page_total is not None:
            if declared_total is not None and declared_total != page_total:
                raise _unavailable()
            declared_total = page_total
        if len(records) + len(batch) > MAX_RESULT_ROWS:
            raise _unavailable()

        page_identities = set()
        for raw in batch:
            if not isinstance(raw, Mapping):
                raise _unavailable()
            identity = _row_identifier(raw)
            if identity is None:
                raise _unavailable()
            page_identities.add(identity[0])
        if batch and not page_identities.difference(identities):
            raise _unavailable()
        identities.update(page_identities)
        records.extend(batch)
        has_more = _strict_has_more(payload)
        if declared_total is not None and len(identities) > declared_total:
            raise _unavailable()

        if has_more is True:
            if (not batch
                    or (declared_total is not None and len(identities) >= declared_total)):
                raise _unavailable()
            continue

        if has_more is False:
            if declared_total is not None and len(identities) != declared_total:
                raise _unavailable()
            completed = True
            break

        # Missing pagination metadata is complete only on a short page. A full
        # page advances the fixed offset once more; duplicate/no-progress,
        # page-count, byte, row, and wall-clock guards still fail closed.
        if declared_total is not None:
            if len(identities) != declared_total:
                raise _unavailable()
            completed = True
            break
        if len(batch) < LEADERBOARD_PAGE_SIZE:
            completed = True
            break
        continue
    if not completed:
        raise _unavailable()
    # API totals/counts describe unique canonical runners. Reject overlap rather
    # than returning a raw list that the sanitizer would silently shrink.
    if len(identities) != len(records):
        raise _unavailable()
    if declared_total is not None and len(identities) != declared_total:
        raise _unavailable()
    return {"status": "ok", "leaderboard": records, "total": len(records), "has_more": False}


class _LoadFlight:
    """One published outcome for callers coalesced on an event load."""

    __slots__ = ("done", "failed", "stale", "value")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.failed = False
        self.stale = False
        self.value: dict[str, list[dict[str, Any]]] | None = None


class CompletedResultsCache:
    """Per-event LRU/TTL cache retaining at most eight sanitized event values.

    Loaders receive only a validated event identifier. :meth:`get` returns a
    deep copy and publishes :attr:`stale` in caller-local state, so concurrent
    callers cannot overwrite one another's freshness status. Expired loads are
    single-flight per event while unrelated event I/O proceeds independently.
    """

    def __init__(
        self,
        results_loader: Callable[[str], Any] = fetch_results_payload,
        leaderboard_loader: Callable[[str], Any] = fetch_leaderboard_payload,
        ttl_seconds: Real = DEFAULT_TTL_SECONDS,
        max_events: int = MAX_CACHED_EVENTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        try:
            ttl = float(ttl_seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("ttl_seconds must be positive and finite") from None
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl_seconds must be positive and finite")
        if type(max_events) is not int or max_events <= 0:
            raise ValueError("max_events must be a positive integer")
        self._results_loader = results_loader
        self._leaderboard_loader = leaderboard_loader
        self._ttl = ttl
        self._max_events = min(max_events, MAX_CACHED_EVENTS)
        self._clock = clock
        # Privacy invariant: every _values value has exactly results/leaderboard,
        # both already minimized. Timestamps and active flights live separately.
        self._values: OrderedDict[str, dict[str, list[dict[str, Any]]]] = OrderedDict()
        self._loaded_at: dict[str, float] = {}
        self._refresh_required: set[str] = set()
        self._flights: dict[str, _LoadFlight] = {}
        self._lock = threading.Lock()
        self._last_clock: float | None = None
        self._clock_high_watermark: float | None = None
        self._caller_state = threading.local()

    @property
    def stale(self) -> bool:
        """Whether this thread's most recent :meth:`get` returned stale data."""
        return bool(getattr(self._caller_state, "stale", False))

    def _copy_for_caller(
        self,
        value: dict[str, list[dict[str, Any]]],
        *,
        stale: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        result = copy.deepcopy(value)
        self._caller_state.stale = stale
        return result

    def _sample_clock_locked(self) -> tuple[float, bool]:
        """Sample a finite clock and return its nondecreasing high watermark."""
        failed = False
        try:
            raw = self._clock()
        except Exception:
            failed = True
            raw = None
        if failed:
            raise _unavailable()
        now = _finite_time(raw)
        if now is None:
            raise _unavailable()
        backward = self._last_clock is not None and now < self._last_clock
        self._last_clock = now
        if self._clock_high_watermark is None or now > self._clock_high_watermark:
            self._clock_high_watermark = now
        return self._clock_high_watermark, backward

    def _load_sanitized(self, event_id: str) -> dict[str, list[dict[str, Any]]]:
        raw_results = self._results_loader(event_id)
        results = sanitize_results_payload(raw_results, event_id=event_id)
        del raw_results
        raw_leaderboard = self._leaderboard_loader(event_id)
        leaderboard = sanitize_leaderboard_payload(raw_leaderboard)
        del raw_leaderboard

        anonymous_ids = set()
        for evidence in leaderboard:
            identity = _row_identifier(evidence)
            if identity is not None and any(evidence.get(flag) is True for flag in _ANONYMITY_FIELDS):
                anonymous_ids.add(identity[0])
        for row in results:
            identity = _row_identifier(row)
            if identity is not None and identity[0] in anonymous_ids:
                row["athlete_anonymous"] = True
                row.pop("name", None)
                row.pop("bib", None)
        return {"results": results, "leaderboard": leaderboard}

    def get(self, event_id: Any) -> dict[str, list[dict[str, Any]]]:
        """Return a deep-copied fresh or event-local stale sanitized source."""
        identifier = validate_identifier(event_id)
        load_started_at = 0.0
        try:
            with self._lock:
                now, clock_went_backward = self._sample_clock_locked()
                existing = self._values.get(identifier)
                if existing is not None and clock_went_backward:
                    self._refresh_required.add(identifier)
                age = (now - self._loaded_at[identifier]
                       if existing is not None else None)
                if (existing is not None and not clock_went_backward
                        and identifier not in self._refresh_required
                        and age is not None and 0 <= age < self._ttl):
                    self._values.move_to_end(identifier)
                    immediate = existing
                    flight = None
                    is_loader = False
                else:
                    immediate = None
                    flight = self._flights.get(identifier)
                    is_loader = flight is None
                    if is_loader:
                        flight = _LoadFlight()
                        self._flights[identifier] = flight
                    load_started_at = now
        except CompletedResultsUnavailable:
            self._caller_state.stale = True
            raise

        if immediate is not None:
            return self._copy_for_caller(immediate, stale=False)

        assert flight is not None
        if not is_loader:
            flight.done.wait()
            if flight.failed or flight.value is None:
                self._caller_state.stale = True
                raise _unavailable()
            return self._copy_for_caller(flight.value, stale=flight.stale)

        load_failed = False
        loaded: dict[str, list[dict[str, Any]]] | None = None
        loaded_at = 0.0
        try:
            try:
                loaded = self._load_sanitized(identifier)
            except Exception:
                load_failed = True
        finally:
            # A loader may be interrupted by KeyboardInterrupt, SystemExit, or
            # another BaseException. Publish a failed/stale outcome and wake all
            # coalesced callers before allowing that interruption to propagate.
            with self._lock:
                try:
                    if not load_failed and loaded is not None:
                        try:
                            loaded_at, completion_went_backward = self._sample_clock_locked()
                        except CompletedResultsUnavailable:
                            load_failed = True
                            loaded_at = 0.0
                            completion_went_backward = True
                        if completion_went_backward or loaded_at < load_started_at:
                            load_failed = True

                    existing = self._values.get(identifier)
                    if load_failed or loaded is None:
                        if existing is None:
                            flight.failed = True
                        else:
                            self._values.move_to_end(identifier)
                            flight.value = existing
                            flight.stale = True
                    else:
                        self._values[identifier] = loaded
                        self._values.move_to_end(identifier)
                        self._loaded_at[identifier] = loaded_at
                        self._refresh_required.discard(identifier)
                        while len(self._values) > self._max_events:
                            evicted, _value = self._values.popitem(last=False)
                            self._loaded_at.pop(evicted, None)
                            self._refresh_required.discard(evicted)
                        flight.value = loaded
                        flight.stale = False
                finally:
                    if self._flights.get(identifier) is flight:
                        self._flights.pop(identifier, None)
                    flight.done.set()

        if flight.failed or flight.value is None:
            self._caller_state.stale = True
            raise _unavailable()
        return self._copy_for_caller(flight.value, stale=flight.stale)
