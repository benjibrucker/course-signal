"""Minimized, cacheable Competitive Timing foot-race catalog.

Only this module performs the fixed catalog request. User-controlled values are
never accepted as hosts, URLs, or paths.
"""
from __future__ import annotations

import copy
import datetime as dt
import bisect
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any

API_HOST = "https://api.competitivetiming.com"
CATALOG_URL = API_HOST + "/races"
FOOT_RACE_TYPES = frozenset({"road", "cross_country", "trail_running"})
RACE_PUBLIC_FIELDS = frozenset({"slug", "name", "location", "race_type", "events"})
EVENT_PUBLIC_FIELDS = frozenset({
    "id", "name", "label", "event_date", "course_status", "live_tracking_enabled",
})
MAX_IDENTIFIER_LENGTH = 200
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 100
MAX_SEARCH_RESULTS = 30
DEFAULT_SEARCH_RESULTS = 20
MAX_SPLITS = 100
MAX_CATALOG_BYTES = 32 * 1024 * 1024
# The inspected provider catalog is below 1,000 races / 10,000 events. These
# structural ceilings leave substantial growth room while also bounding
# injected loaders, whose values do not pass through the network byte limit.
MAX_CATALOG_RACES = 10_000
MAX_EVENTS_PER_RACE = 2_000
MAX_TOTAL_CATALOG_EVENTS = 100_000
MAX_SOURCE_TEXT_CHARS = 1_024
MAX_CLOCK_SECONDS = 1e12
CATALOG_TTL_SECONDS = 15 * 60
# Maximum total age since a successful catalog fetch, inclusive.
CATALOG_MAX_STALE_SECONDS = 24 * 60 * 60
_IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_NON_FOOT = re.compile(
    r"(?:^|[^a-z0-9])(?:handcycles?|wheelchairs?|soapboxes?|bicycles?|bikes?|cycling|"
    r"triathlons?|duathlons?|skis?|skiing|snowshoes?|kayaks?|canoes?|paddles?|paddling)"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)


class CatalogError(Exception):
    """Base class for bounded catalog failures."""


class InvalidIdentifier(CatalogError, ValueError):
    pass


class UnknownEvent(CatalogError, LookupError):
    pass


class CatalogQueryError(CatalogError, ValueError):
    pass


class CatalogUnavailable(CatalogError, RuntimeError):
    pass


def _unavailable() -> CatalogUnavailable:
    return CatalogUnavailable("catalog unavailable")


def validate_identifier(value: Any) -> str:
    """Return a safe catalog identifier or fail closed.

    Competitive Timing's public IDs are lowercase, hyphen-separated tokens.
    This grammar excludes URLs, path separators, traversal, percent encoding,
    query fragments, whitespace, and control characters.
    """
    if (not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH
            or _IDENTIFIER.fullmatch(value) is None):
        raise InvalidIdentifier("invalid identifier")
    return value


def _text(value: Any, maximum: int = 300) -> str:
    if type(value) is not str:
        return ""
    # Slice before whitespace normalization so injected overlong strings cannot
    # force unbounded split/join work. All public maxima are below this ceiling.
    source = value[:MAX_SOURCE_TEXT_CHARS]
    clean = " ".join(source.split())
    return clean[:maximum]


def _date(value: Any) -> str | None:
    if type(value) is not str or len(value) < 10:
        return None
    candidate = value[:10]
    try:
        dt.date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _time(value: Any) -> str | None:
    if type(value) is not str or not value:
        return None
    candidate = value[:15]
    try:
        dt.time.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _distance(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _has_oversized_searchable_text(raw: dict[str, Any]) -> bool:
    for field in ("id", "slug", "name", "display_name", "category"):
        value = raw.get(field)
        if isinstance(value, str) and (
                type(value) is not str or len(value) > MAX_SOURCE_TEXT_CHARS):
            return True
    return False


def _non_foot_record(raw: dict[str, Any]) -> bool:
    for field in ("derby_config", "slalom_config"):
        if raw.get(field) not in (None, False, {}, []):
            return True
    fields = ("id", "slug", "name", "display_name", "category")
    if _has_oversized_searchable_text(raw):
        return True
    searchable = " ".join(_text(raw.get(field), 500) for field in fields)
    return _NON_FOOT.search(searchable) is not None


def _event_summary(raw: Any, race_slug: str) -> dict[str, Any] | None:
    if (type(raw) is not dict or raw.get("hidden") is True or raw.get("is_test") is True
            or _non_foot_record(raw)):
        return None
    try:
        event_id = validate_identifier(raw.get("id"))
    except InvalidIdentifier:
        return None
    embedded_race = raw.get("race_id")
    if (embedded_race is not None
            and (type(embedded_race) is not str
                 or len(embedded_race) > MAX_SOURCE_TEXT_CHARS
                 or embedded_race != race_slug)):
        return None
    summary = {
        "id": event_id,
        "name": _text(raw.get("name")),
        "event_date": _date(raw.get("event_date")),
        "course_status": _text(raw.get("course_status"), 40) or "unknown",
        "live_tracking_enabled": raw.get("live_tracking_enabled") is True,
    }
    label = _text(raw.get("display_name"))
    if label:
        summary["label"] = label
    return summary


def _validate_catalog_shape(raw_races: Any) -> list[Any]:
    """Apply O(1) outer caps and a bounded aggregate event pre-pass."""
    if type(raw_races) is not list or len(raw_races) > MAX_CATALOG_RACES:
        raise _unavailable()
    total_events = 0
    for raw in raw_races:
        if type(raw) is not dict:
            continue
        raw_events = raw.get("events")
        if type(raw_events) is not list:
            continue
        event_count = len(raw_events)
        if event_count > MAX_EVENTS_PER_RACE:
            raise _unavailable()
        total_events += event_count
        if total_events > MAX_TOTAL_CATALOG_EVENTS:
            raise _unavailable()
    return raw_races


def _foot_race_type(value: Any) -> bool:
    return type(value) is str and len(value) <= 32 and value in FOOT_RACE_TYPES


def sanitize_catalog(payload: Any) -> list[dict[str, Any]]:
    """Pure allowlist transform from the large raw catalog to public rows."""
    if type(payload) is not dict or payload.get("status") not in (None, "ok"):
        raise _unavailable()
    raw_races = _validate_catalog_shape(payload.get("races"))
    output = []
    seen_slugs: set[str] = set()
    for raw in raw_races:
        if (type(raw) is not dict or not _foot_race_type(raw.get("race_type"))
                or raw.get("hidden") is True or _has_oversized_searchable_text(raw)):
            continue
        try:
            slug = validate_identifier(raw.get("slug"))
        except InvalidIdentifier:
            continue
        raw_id = raw.get("id")
        if raw_id is not None:
            try:
                if validate_identifier(raw_id) != slug:
                    continue
            except InvalidIdentifier:
                continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        events_value = raw.get("events")
        raw_events = events_value if type(events_value) is list else []
        events = [summary for event in raw_events
                  if (summary := _event_summary(event, slug)) is not None]
        if (raw_events and not events) or (not raw_events and _non_foot_record(raw)):
            continue
        events.sort(key=lambda event: (event["event_date"] or "", event["id"]), reverse=True)
        output.append({
            "slug": slug,
            "name": _text(raw.get("name")) or slug.replace("-", " ").title(),
            "location": _text(raw.get("location")),
            "race_type": raw["race_type"],
            "events": events,
        })
    output.sort(key=lambda race: (race["name"].casefold(), race["slug"]))
    return output


def _query(value: Any) -> str:
    if type(value) is not str or len(value) > MAX_QUERY_LENGTH:
        raise CatalogQueryError("invalid query")
    query = " ".join(value.split())
    if len(query) < MIN_QUERY_LENGTH or any(ord(char) < 32 for char in value):
        raise CatalogQueryError("invalid query")
    return query.casefold()


def parse_year(value: Any) -> int | None:
    if value is None or (type(value) is str and value == ""):
        return None
    if type(value) is int:
        year = value
    elif (type(value) is str and len(value) == 4
          and value.isascii() and value.isdigit()):
        year = int(value)
    else:
        raise CatalogQueryError("invalid year")
    if not 1900 <= year <= 2100:
        raise CatalogQueryError("invalid year")
    return year


def parse_limit(value: Any) -> int:
    if value is None or (type(value) is str and value == ""):
        return DEFAULT_SEARCH_RESULTS
    if type(value) is int:
        limit = value
    elif (type(value) is str and len(value) <= MAX_QUERY_LENGTH
          and value.isascii() and value.isdigit()):
        canonical = value.lstrip("0") or "0"
        maximum = str(MAX_SEARCH_RESULTS)
        if (len(canonical) > len(maximum)
                or (len(canonical) == len(maximum) and canonical > maximum)):
            return MAX_SEARCH_RESULTS
        limit = int(canonical)
    else:
        raise CatalogQueryError("invalid limit")
    if limit < 1:
        raise CatalogQueryError("invalid limit")
    return min(limit, MAX_SEARCH_RESULTS)


def validate_search_parameters(query: Any, year: Any = None, limit: Any = None) -> tuple[str, int | None, int]:
    """Validate all search inputs before a catalog cache or network lookup."""
    return _query(query), parse_year(year), parse_limit(limit)


def _search_values(race: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    values = [race.get("slug"), str(race.get("slug") or "").replace("-", " "),
              race.get("name"), race.get("location")]
    for event in events:
        values.extend((event.get("id"), str(event.get("id") or "").replace("-", " "),
                       event.get("name"), event.get("label")))
    return [str(value).casefold() for value in values if value]


def search_catalog(races: Iterable[dict[str, Any]], query: Any, year: Any = None,
                   limit: Any = None) -> list[dict[str, Any]]:
    """Search an already-sanitized catalog without returning the full source."""
    needle, selected_year, maximum = validate_search_parameters(query, year=year, limit=limit)
    ranked: list[tuple[tuple[int, str, str, int], dict[str, Any], list[dict[str, Any]]]] = []
    for source_index, race in enumerate(races):
        if not isinstance(race, dict):
            continue
        events = [event for event in race.get("events") or []
                  if selected_year is None or str(event.get("event_date") or "").startswith(f"{selected_year:04d}-")]
        if selected_year is not None and not events:
            continue
        values = _search_values(race, events)
        exact = any(value == needle for value in values)
        prefix = any(value.startswith(needle) for value in values)
        substring = any(needle in value for value in values)
        if not substring:
            continue
        score = 0 if exact else 1 if prefix else 2
        rank = (score, str(race.get("name") or "").casefold(),
                str(race.get("slug") or ""), source_index)
        bisect.insort(ranked, (rank, race, events))
        if len(ranked) > maximum:
            ranked.pop()

    results = []
    for _rank, race, events in ranked:
        result = {key: copy.deepcopy(race.get(key))
                  for key in RACE_PUBLIC_FIELDS if key != "events"}
        result["events"] = copy.deepcopy(events)
        results.append(result)
    return results


def find_event(races: Iterable[dict[str, Any]], event_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one event from one allowlisted foot-race record, or fail closed."""
    identifier = validate_identifier(event_id)
    matches = []
    for race in races:
        if not isinstance(race, dict) or race.get("race_type") not in FOOT_RACE_TYPES:
            continue
        for event in race.get("events") or []:
            if isinstance(event, dict) and event.get("id") == identifier:
                matches.append((race, event))
    if len(matches) != 1:
        raise UnknownEvent("event not found")
    race, event = matches[0]
    return copy.deepcopy(race), copy.deepcopy(event)


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


def fetch_catalog_payload() -> dict[str, Any]:
    """Fetch the one fixed upstream catalog URL with a bounded response size."""
    request = urllib.request.Request(CATALOG_URL, headers={
        "Accept": "application/json",
        "Origin": "https://competitivetiming.com",
        "Referer": "https://competitivetiming.com/",
        "User-Agent": "CompetitiveTimingCatalog/1.0 (+read-only spectator display)",
    })
    failed = False
    payload: Any = None
    try:
        with _open_fixed_host(request, timeout=30) as response:
            if (not _is_fixed_api_origin(response.geturl())
                    or getattr(response, "status", None) != 200):
                raise _unavailable()
            body = response.read(MAX_CATALOG_BYTES + 1)
        if len(body) > MAX_CATALOG_BYTES:
            raise _unavailable()
        payload = json.loads(body)
        if type(payload) is not dict:
            raise _unavailable()
    except Exception:
        failed = True
    if failed or payload is None:
        raise _unavailable()
    return payload


class CatalogCache:
    """Per-instance TTL cache that retains only the minimized catalog.

    ``stale`` describes this thread's latest :meth:`get`, preventing another
    request thread from relabeling a value between ``get()`` and serialization.
    """

    def __init__(self, loader: Callable[[], Any] = fetch_catalog_payload,
                 ttl_seconds: int = CATALOG_TTL_SECONDS,
                 max_stale_seconds: float | None = CATALOG_MAX_STALE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._loader = loader
        self._ttl = max(1, int(ttl_seconds))
        if max_stale_seconds is not None and (
                isinstance(max_stale_seconds, bool) or not math.isfinite(max_stale_seconds)
                or max_stale_seconds < 0):
            raise ValueError("max_stale_seconds must be finite and nonnegative")
        self._max_stale = max_stale_seconds
        self._clock = clock
        self._value: tuple[dict[str, Any], ...] | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()
        self._last_clock: float | None = None
        self._caller_state = threading.local()

    @property
    def stale(self) -> bool:
        """Whether this thread's most recent :meth:`get` returned stale data."""
        return bool(getattr(self._caller_state, "stale", False))

    def _sample_clock_locked(self) -> tuple[float, bool]:
        """Return one validated sample and whether the clock moved backward."""
        failed = False
        try:
            raw = self._clock()
        except Exception:
            failed = True
            raw = None
        if type(raw) is int:
            now = float(raw) if 0 <= raw <= MAX_CLOCK_SECONDS else None
        elif type(raw) is float:
            now = raw if math.isfinite(raw) and 0 <= raw <= MAX_CLOCK_SECONDS else None
        else:
            now = None
        if failed or now is None:
            raise _unavailable()
        backward = self._last_clock is not None and now < self._last_clock
        self._last_clock = now
        return now, backward

    def get(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            try:
                now, clock_went_backward = self._sample_clock_locked()
            except CatalogUnavailable:
                self._caller_state.stale = False
                raise
            age = now - self._loaded_at if self._value is not None else None
            if (self._value is not None and not clock_went_backward
                    and age is not None and 0 <= age < self._ttl):
                self._caller_state.stale = False
                return self._value

            load_started_at = now
            load_failed = False
            value: tuple[dict[str, Any], ...] | None = None
            try:
                value = tuple(sanitize_catalog(self._loader()))
            except Exception:
                load_failed = True

            if load_failed:
                clock_failed = False
                try:
                    failed_at, failure_went_backward = self._sample_clock_locked()
                except CatalogUnavailable:
                    clock_failed = True
                    failed_at = 0.0
                    failure_went_backward = True
                age = failed_at - self._loaded_at if self._value is not None else None
                if (not clock_failed and self._value is not None
                        and not clock_went_backward and not failure_went_backward
                        and age is not None and 0 <= age
                        and (self._max_stale is None or age <= self._max_stale)):
                    self._caller_state.stale = True
                    return self._value

                self._caller_state.stale = False
                raise _unavailable()

            completion_clock_failed = False
            try:
                loaded_at, completion_went_backward = self._sample_clock_locked()
            except CatalogUnavailable:
                completion_clock_failed = True
                loaded_at = 0.0
                completion_went_backward = True
            if (completion_clock_failed or completion_went_backward
                    or loaded_at < load_started_at or value is None):
                self._caller_state.stale = False
                raise _unavailable()

            self._value = value
            self._loaded_at = loaded_at
            self._caller_state.stale = False
            return self._value
