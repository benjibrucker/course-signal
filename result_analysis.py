"""Pure completed-runner split, field, terrain, and energy analysis.

The module accepts already-fetched public source payloads and never performs I/O.
Every returned participant field is allowlisted. Split zero is recorded as chip
elapsed zero only when an explicit source read agrees with the runner's verified
chip-start offset (or is the canonical zero read).

``field_percentile`` is a same-section midrank percentile: the percentage of
valid durations that are slower than the target, plus half of tied durations.
It is descriptive of the supplied result cohort, not a population estimate.

Terrain effort reuses :mod:`terrain_model`'s bounded Minetti transport profile.
``cumulative_effort_m`` is flat-cost-normalized distance, so active transport
energy is effort metres * 3.6 J/kg/m / 4184 J/kcal. The accompanying +/-25%
range is explicitly a non-statistical communication range.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
from numbers import Real
import re
import statistics
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import unicodedata

from finish_metrics import prepare_route
from terrain_model import TerrainProfile, build_profile

MAX_QUERY_LENGTH = 100
MAX_SEARCH_LIMIT = 50
MAX_RESULT_ROWS = 100_000
MAX_EVIDENCE_ROWS = 200_000
MAX_RUNNER_ID = 9_007_199_254_740_991
MAX_RUNNER_ID_DIGITS = len(str(MAX_RUNNER_ID))
MAX_EVENT_ID_LENGTH = 200
MAX_CHIP_SECONDS = 1e12
METERS_PER_MILE = 1609.344
JOULES_PER_KILOCALORIE = 4184.0
FLAT_RUNNING_JOULES_PER_KG_M = 3.6
ENERGY_RANGE_FRACTION = 0.25
SPLIT_TIME_TOLERANCE_SECONDS = 0.01

_EVENT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_EVENT_ID_FIELDS = ("event_id", "eventId")
_ANONYMITY_FIELDS = ("is_anonymous", "athlete_anonymous")

FIELD_PERCENTILE_DEFINITION = (
    "Same-section midrank percentile: 100 * (slower durations + 0.5 * tied "
    "durations) / valid field count; higher is faster within the supplied cohort."
)
ACTIVE_ENERGY_RANGE_DEFINITION = (
    "Non-statistical communication range of midpoint +/-25%; transport energy "
    "per kg only, excluding resting energy."
)

_UNKNOWN_SECTION_FIELDS = {
    "segment_seconds": None,
    "cumulative_seconds": None,
    "cumulative_place": None,
    "segment_place": None,
    "rank_change": None,
    "field_count": None,
    "field_median_seconds": None,
    "field_percentile": None,
    "age_group_count": None,
    "age_group_median_seconds": None,
    "age_group_percentile": None,
    "distance_m": None,
    "gain_m": None,
    "loss_m": None,
    "net_grade": None,
    "pace_seconds_per_mile": None,
    "terrain_adjusted_effort_m": None,
    "active_energy_kcal_per_kg": None,
    "active_energy_communication_range_kcal_per_kg": None,
}


@dataclass(frozen=True)
class _NormalizedResult:
    valid: bool
    reason: Optional[str]
    times: Mapping[int, float]
    cumulative_places: Mapping[int, int]
    segment_places: Mapping[int, int]
    status: str
    last_index: Optional[int]
    finish_seconds: Optional[float]
    finish_place: Optional[int]


@dataclass(frozen=True)
class _Geometry:
    profile: TerrainProfile
    progresses: Tuple[float, ...]
    route_total_m: float


def _number(value: Any, *, minimum: Optional[float] = None) -> Optional[float]:
    """Accept finite numeric source values, never booleans or numeric strings."""
    try:
        if not isinstance(value, Real) or isinstance(value, bool):
            return None
        result = float(value)
        if not math.isfinite(result) or (minimum is not None and result < minimum):
            return None
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def _integer(value: Any, *, minimum: Optional[int] = None) -> Optional[int]:
    if type(value) is not int:
        return None
    if minimum is not None and value < minimum:
        return None
    return value


def _chip_seconds(value: Any, *, allow_zero: bool = True) -> Optional[float]:
    seconds = _number(value, minimum=0.0)
    if seconds is None or seconds > MAX_CHIP_SECONDS or (not allow_zero and seconds == 0):
        return None
    return seconds


def _identifier(value: Any) -> Optional[int]:
    """Competitive Timing runner IDs are integral; do not round float IDs."""
    if type(value) is int:
        return value if 0 <= value <= MAX_RUNNER_ID else None
    if (isinstance(value, str) and value and len(value) <= MAX_RUNNER_ID_DIGITS
            and value.isascii() and value.isdigit()):
        parsed = int(value)
        return parsed if parsed <= MAX_RUNNER_ID else None
    return None


def _event_identifier(value: Any) -> Optional[str]:
    if (not isinstance(value, str) or not value or len(value) > MAX_EVENT_ID_LENGTH
            or _EVENT_ID.fullmatch(value) is None):
        return None
    return value


def _event_aliases_match(record: Mapping[str, Any], expected: str,
                         *, required: bool = False) -> bool:
    values = [record[field] for field in _EVENT_ID_FIELDS if field in record]
    if not values:
        return not required
    return all(_event_identifier(value) == expected for value in values)


def _record_identifier(record: Mapping[str, Any]) -> Optional[int]:
    identifiers = []
    for field in ("id", "runner_id"):
        if field not in record or record.get(field) is None:
            continue
        identifier = _identifier(record.get(field))
        if identifier is None:
            return None
        identifiers.append(identifier)
    if not identifiers or any(value != identifiers[0] for value in identifiers[1:]):
        return None
    return identifiers[0]


def _public_text(value: Any, fallback: str, maximum: int = 200) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    return value[:maximum] if value else fallback


def _public_bib(value: Any) -> Any:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        value = value.strip()
        if value and len(value) <= 64:
            return value
    return None


def _public_age(value: Any) -> Optional[int]:
    age = _integer(value, minimum=1)
    return age if age is not None and age <= 120 else None


def _public_optional_text(value: Any, maximum: int = 64) -> Optional[str]:
    text = _public_text(value, "", maximum)
    return text or None


def _fold_search_text(value: str) -> str:
    """Casefold and remove combining marks while preserving public output."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _positive_place(value: Any) -> Optional[int]:
    return value if type(value) is int and value > 0 else None


def _place_from(record: Mapping[str, Any], names: Iterable[str]) -> Optional[int]:
    for name in names:
        if name in record:
            return _positive_place(record.get(name))
    return None


def _status(record: Mapping[str, Any], finish_index: Optional[int] = None) -> str:
    if record.get("dns") is True:
        return "DNS"
    if record.get("dq") is True:
        return "DQ"
    if record.get("dnq") is True:
        return "DNQ"
    if record.get("dnf") is True or record.get("dropped") is True:
        return "DNF"
    raw_status = record.get("status")
    explicit = (raw_status.strip().upper().replace(" ", "_")
                if isinstance(raw_status, str) else "")
    aliases = {
        "FINISH": "FINISHED",
        "FINISHER": "FINISHED",
        "COMPLETE": "FINISHED",
        "COMPLETED": "FINISHED",
        "DROPPED": "DNF",
        "DID_NOT_FINISH": "DNF",
        "DID_NOT_START": "DNS",
        "DISQUALIFIED": "DQ",
    }
    explicit = aliases.get(explicit, explicit)
    if explicit in {"FINISHED", "DNF", "DNS", "DQ", "DNQ"}:
        return explicit
    finish = _chip_seconds(record.get("finish_time_seconds"), allow_zero=False)
    if finish is not None:
        return "FINISHED"
    index = _integer(record.get("last_split_index"), minimum=0)
    if finish_index is not None and index == finish_index:
        return "FINISHED"
    return "INCOMPLETE"


def _raw_sources(raw_results: Any) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    """Return authoritative result rows plus privacy evidence from known sources."""
    if isinstance(raw_results, (list, tuple)):
        if len(raw_results) > MAX_RESULT_ROWS:
            raise ValueError("raw_results exceeds safe cardinality")
        results = list(raw_results)
        evidence = list(results)
    elif isinstance(raw_results, dict):
        if raw_results.get("status") not in (None, "ok"):
            raise ValueError("raw_results status is not usable")
        source_results = raw_results.get("results")
        if not isinstance(source_results, (list, tuple)):
            raise TypeError("raw_results['results'] must be a list")
        if len(source_results) > MAX_RESULT_ROWS:
            raise ValueError("raw_results exceeds safe cardinality")
        results = list(source_results)
        if "has_more" in raw_results and raw_results.get("has_more") is not False:
            raise ValueError("raw_results is incomplete")
        for key in ("total", "total_count", "count"):
            if key in raw_results:
                total = _integer(raw_results.get(key), minimum=0)
                if total is None or total != len(results):
                    raise ValueError("raw_results count metadata conflicts")
        evidence = list(results)
        for key in ("leaderboard", "positions"):
            rows = raw_results.get(key)
            if isinstance(rows, (list, tuple)):
                if len(evidence) + len(rows) > MAX_EVIDENCE_ROWS:
                    raise ValueError("raw_results exceeds safe evidence cardinality")
                evidence.extend(rows)
        gps = raw_results.get("gps_response")
        if isinstance(gps, dict) and isinstance(gps.get("positions"), (list, tuple)):
            if len(evidence) + len(gps["positions"]) > MAX_EVIDENCE_ROWS:
                raise ValueError("raw_results exceeds safe evidence cardinality")
            evidence.extend(gps["positions"])
    else:
        raise TypeError("raw_results must be a list or a results payload")
    return ([row for row in results if isinstance(row, Mapping)],
            [row for row in evidence if isinstance(row, Mapping)])


def _anonymous_ids(evidence: Sequence[Mapping[str, Any]]) -> set[int]:
    anonymous = set()
    for row in evidence:
        runner_id = _record_identifier(row)
        if runner_id is not None and any(row.get(field) is True for field in _ANONYMITY_FIELDS):
            anonymous.add(runner_id)
    return anonymous


def _first_results_by_id(results: Sequence[Mapping[str, Any]]) -> Dict[int, Mapping[str, Any]]:
    output: Dict[int, Mapping[str, Any]] = {}
    for record in results:
        runner_id = _record_identifier(record)
        if runner_id is not None and runner_id not in output:
            output[runner_id] = record
    return output


def _finish_summary(record: Mapping[str, Any], status: str) -> Optional[float]:
    finish = _chip_seconds(record.get("finish_time_seconds"), allow_zero=False)
    return finish if status == "FINISHED" else None


def _public_identity(record: Mapping[str, Any], runner_id: int, anonymous: bool) -> Dict[str, Any]:
    status = _status(record)
    return {
        "id": runner_id,
        "name": "Anonymous Participant" if anonymous else _public_text(record.get("name"), "Unknown Runner"),
        "bib": None if anonymous else _public_bib(record.get("bib")),
        "age": None if anonymous else _public_age(record.get("age")),
        "gender": None if anonymous else _public_optional_text(record.get("gender")),
        "age_group": None if anonymous else _public_optional_text(record.get("age_group")),
        "age_group_place": None if anonymous else _place_from(
            record, ("chip_age_group_place", "gun_age_group_place")
        ),
        "gender_place": None if anonymous else _place_from(
            record, ("chip_gender_place", "gun_gender_place")
        ),
        "status": status,
        "finish_seconds": _finish_summary(record, status),
        "finish_place": _place_from(record, ("finish_place", "overall_place", "place"))
        if status == "FINISHED" else None,
    }


def _age_gender_cohort_key(record: Mapping[str, Any], anonymous: bool) -> Optional[Tuple[str, str]]:
    if anonymous:
        return None
    gender = _public_optional_text(record.get("gender"))
    age_group = _public_optional_text(record.get("age_group"))
    if gender is None or age_group is None:
        return None
    return gender.casefold(), age_group.casefold()


def search_public_runners(raw_results: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    """Search only public names/bibs with exact, prefix, then stable substring order."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    query = query.strip()
    if not query or len(query) > MAX_QUERY_LENGTH:
        raise ValueError("query must contain 1..100 characters")
    if type(limit) is not int:
        raise TypeError("limit must be an integer")
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError("limit must be between 1 and 50")

    results, evidence = _raw_sources(raw_results)
    folded_query = _fold_search_text(query)
    anonymous_ids = _anonymous_ids(evidence)
    matches: Tuple[List[Dict[str, Any]], ...] = ([], [], [])
    for runner_id, record in _first_results_by_id(results).items():
        public = _public_identity(record, runner_id, runner_id in anonymous_ids)
        candidates = [public["name"]]
        if public["bib"] is not None:
            candidates.append(str(public["bib"]))
        values = [_fold_search_text(value) for value in candidates]
        if not any(folded_query in value for value in values):
            continue
        score = 0 if any(value == folded_query for value in values) else (
            1 if any(value.startswith(folded_query) for value in values) else 2
        )
        if len(matches[score]) < limit:
            matches[score].append(public)
    return [public for bucket in matches for public in bucket][:limit]


def _invalid_result(reason: str) -> _NormalizedResult:
    return _NormalizedResult(False, reason, {}, {}, {}, "INCOMPLETE", None, None, None)


def _normalise_result(record: Mapping[str, Any], runner_id: int, event_id: Any,
                      split_count: int) -> _NormalizedResult:
    if _record_identifier(record) != runner_id:
        return _invalid_result("runner identity mismatch")
    if not _event_aliases_match(record, event_id):
        return _invalid_result("event identity mismatch")

    status = _status(record, split_count - 1)
    splits = record.get("splits")
    if not isinstance(splits, (list, tuple)) or not splits:
        return _invalid_result("split history unavailable")

    offset = _chip_seconds(record.get("chip_start_seconds"))
    times: Dict[int, float] = {}
    cumulative_places: Dict[int, int] = {}
    segment_places: Dict[int, int] = {}
    previous_index = -1
    previous_nonstart_time = -1.0
    for split in splits:
        if not isinstance(split, Mapping):
            return _invalid_result("malformed split row")
        index = _integer(split.get("split_index"), minimum=0)
        elapsed = _chip_seconds(split.get("elapsed_seconds"))
        if index is None or index >= split_count or index <= previous_index or elapsed is None:
            return _invalid_result("invalid split bounds, order, or time")
        previous_index = index
        cumulative_place = _place_from(split, ("cumulative_place", "overall_place", "place", "rank"))
        segment_place = _place_from(split, ("segment_place", "split_place", "interval_place", "segment_rank"))
        if cumulative_place is not None:
            cumulative_places[index] = cumulative_place
        if segment_place is not None:
            segment_places[index] = segment_place
        if index == 0:
            if offset is not None and (elapsed == 0 or abs(elapsed - offset) <= SPLIT_TIME_TOLERANCE_SECONDS):
                times[0] = 0.0
            continue
        if elapsed <= 0 or elapsed <= previous_nonstart_time:
            return _invalid_result("non-increasing split times")
        previous_nonstart_time = elapsed
        times[index] = elapsed

    raw_last_index = record.get("last_split_index")
    raw_last_time = record.get("last_split_time")
    if raw_last_index is None and raw_last_time is None:
        last_index = max(times) if times else None
        last_time = times.get(last_index) if last_index is not None else None
    else:
        last_index = _integer(raw_last_index, minimum=0)
        last_time = _chip_seconds(raw_last_time)
        if last_index is None or last_index >= split_count or last_time is None:
            return _invalid_result("invalid latest split summary")
        normalized_last = 0.0 if last_index == 0 else last_time
        if last_index not in times or abs(times[last_index] - normalized_last) > SPLIT_TIME_TOLERANCE_SECONDS:
            return _invalid_result("latest split summary mismatch")
        if max(times) != last_index:
            return _invalid_result("split occurs after latest summary")

    finish_value = record.get("finish_time_seconds")
    finish_seconds = None
    if finish_value is not None:
        finish_seconds = _chip_seconds(finish_value, allow_zero=False)
        if finish_seconds is None:
            return _invalid_result("invalid finish time")
        if (last_index != split_count - 1 or split_count - 1 not in times
                or abs(times[split_count - 1] - finish_seconds) > SPLIT_TIME_TOLERANCE_SECONDS):
            return _invalid_result("finish summary mismatch")
    if status == "FINISHED":
        if last_index != split_count - 1 or split_count - 1 not in times:
            return _invalid_result("finished status lacks finish split")
        if finish_seconds is None:
            finish_seconds = times[split_count - 1]
    elif split_count - 1 in times:
        return _invalid_result("incomplete result contains finish split")

    finish_place = _place_from(record, ("finish_place", "overall_place", "place"))
    if finish_place is None:
        finish_place = cumulative_places.get(split_count - 1)
    return _NormalizedResult(
        True,
        None,
        dict(times),
        dict(cumulative_places),
        dict(segment_places),
        status,
        last_index,
        finish_seconds,
        finish_place,
    )


def _event_contract(event_response: Any) -> Tuple[Any, List[str]]:
    if not isinstance(event_response, Mapping) or not isinstance(event_response.get("event"), Mapping):
        raise ValueError("event response is unavailable")
    event = event_response["event"]
    event_id = _event_identifier(event.get("id"))
    names = event.get("split_names")
    if event_id is None or not isinstance(names, (list, tuple)) or len(names) < 2:
        raise ValueError("official event identity/splits are unavailable")
    output = []
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("official split names are malformed")
        output.append(name.strip())
    return event_id, output


def _prepare_geometry(event_id: Any, course: Any, split_progresses: Any,
                      split_count: int) -> Optional[_Geometry]:
    if not isinstance(course, Mapping):
        return None
    if not _event_aliases_match(course, event_id, required=True):
        return None
    if not isinstance(split_progresses, (list, tuple)) or len(split_progresses) != split_count:
        return None
    progresses = []
    for value in split_progresses:
        number = _number(value)
        if number is None or not 0 <= number <= 1:
            return None
        progresses.append(number)
    if (progresses[0] != 0 or progresses[-1] != 1
            or any(right <= left for left, right in zip(progresses, progresses[1:]))):
        return None

    track = course.get("trackPoints")
    if not isinstance(track, (list, tuple)) or len(track) < 2:
        return None
    coordinates = []
    elevations = []
    for point in track:
        if not isinstance(point, Mapping):
            return None
        latitude = _number(point.get("lat"))
        longitude = _number(point.get("lng"))
        elevation = _number(point.get("ele"))
        if (latitude is None or longitude is None or elevation is None
                or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
            return None
        coordinates.append((latitude, longitude))
        elevations.append(elevation)
    route = prepare_route(tuple(coordinates))
    if route.total <= 0 or len(route.cumulative) != len(elevations):
        return None
    profile = build_profile(list(route.cumulative), elevations)
    if profile is None:
        return None
    return _Geometry(profile, tuple(progresses), route.total)


def _interpolate(x: Sequence[float], y: Sequence[float], value: float) -> float:
    if value <= x[0]:
        return y[0]
    if value >= x[-1]:
        return y[-1]
    index = bisect_right(x, value) - 1
    fraction = (value - x[index]) / (x[index + 1] - x[index])
    return y[index] + fraction * (y[index + 1] - y[index])


def _terrain_section(geometry: _Geometry, start_index: int, end_index: int,
                     segment_seconds: float) -> Optional[Dict[str, Any]]:
    profile = geometry.profile
    start_m = geometry.progresses[start_index] * geometry.route_total_m
    end_m = geometry.progresses[end_index] * geometry.route_total_m
    if not 0 <= start_m < end_m <= geometry.route_total_m:
        return None
    distances = profile.cumulative_m
    elevations = profile.elevation_m
    effort = profile.cumulative_effort_m
    sample_distances = [start_m]
    sample_distances.extend(value for value in distances if start_m < value < end_m)
    sample_distances.append(end_m)
    heights = [_interpolate(distances, elevations, value) for value in sample_distances]
    gain = sum(max(0.0, right - left) for left, right in zip(heights, heights[1:]))
    loss = sum(max(0.0, left - right) for left, right in zip(heights, heights[1:]))
    distance = end_m - start_m
    terrain_effort = _interpolate(distances, effort, end_m) - _interpolate(distances, effort, start_m)
    if not all(math.isfinite(value) for value in (distance, gain, loss, terrain_effort)):
        return None
    if distance <= 0 or terrain_effort <= 0:
        return None
    energy = terrain_effort * FLAT_RUNNING_JOULES_PER_KG_M / JOULES_PER_KILOCALORIE
    pace = segment_seconds * METERS_PER_MILE / distance
    if not math.isfinite(energy) or energy <= 0 or not math.isfinite(pace) or pace <= 0:
        return None
    return {
        "distance_m": distance,
        "gain_m": gain,
        "loss_m": loss,
        "net_grade": (heights[-1] - heights[0]) / distance,
        "pace_seconds_per_mile": pace,
        "terrain_adjusted_effort_m": terrain_effort,
        "active_energy_kcal_per_kg": energy,
        "active_energy_communication_range_kcal_per_kg": {
            "lower": energy * (1 - ENERGY_RANGE_FRACTION),
            "upper": energy * (1 + ENERGY_RANGE_FRACTION),
        },
    }


def _recorded_duration(result: _NormalizedResult, start_index: int,
                       end_index: int) -> Optional[float]:
    if start_index not in result.times or end_index not in result.times:
        return None
    duration = result.times[end_index] - result.times[start_index]
    return duration if math.isfinite(duration) and duration > 0 else None


def _field_values(normalized: Sequence[_NormalizedResult], start_index: int,
                  end_index: int) -> List[float]:
    values = []
    for result in normalized:
        if not result.valid:
            continue
        duration = _recorded_duration(result, start_index, end_index)
        if duration is not None:
            values.append(duration)
    return values


def _field_percentile(values: Sequence[float], target: float) -> float:
    slower = sum(value > target for value in values)
    tied = sum(value == target for value in values)
    return 100.0 * (slower + 0.5 * tied) / len(values)


def _summary_section(row: Mapping[str, Any], value_name: str) -> Dict[str, Any]:
    return {
        "start_index": row["start_index"],
        "start_name": row["start_name"],
        "end_index": row["end_index"],
        "end_name": row["end_name"],
        value_name: row[value_name],
    }


def _descriptive_summary(sections: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    comparable = [row for row in sections
                  if row["field_count"] is not None and row["field_count"] >= 2
                  and row["field_percentile"] is not None]
    strongest = max(comparable, key=lambda row: row["field_percentile"]) if comparable else None
    weakest = min(comparable, key=lambda row: row["field_percentile"]) if comparable else None
    gains = [row for row in sections if row["rank_change"] is not None and row["rank_change"] > 0]
    losses = [row for row in sections if row["rank_change"] is not None and row["rank_change"] < 0]

    if len(comparable) >= 3:
        values = [row["field_percentile"] for row in comparable]
        consistency = {
            "status": "available",
            "comparable_section_count": len(values),
            "mean_percentile": statistics.mean(values),
            "percentile_range": max(values) - min(values),
        }
        indices = list(range(len(values)))
        x_mean = statistics.mean(indices)
        y_mean = statistics.mean(values)
        denominator = sum((value - x_mean) ** 2 for value in indices)
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(indices, values)) / denominator
        direction = (
            "higher_relative_percentiles_later" if slope > 0
            else "lower_relative_percentiles_later" if slope < 0
            else "no_relative_change"
        )
        trend = {
            "status": "available",
            "comparable_section_count": len(values),
            "percentile_points_per_section": slope,
            "direction": direction,
        }
    else:
        unavailable = {
            "status": "insufficient_comparable_sections",
            "comparable_section_count": len(comparable),
        }
        consistency = dict(unavailable)
        trend = dict(unavailable)

    return {
        "comparison_cohort": "overall_field",
        "valid_section_count": len(comparable),
        "strongest_comparable_section": _summary_section(strongest, "field_percentile") if strongest else None,
        "weakest_comparable_section": _summary_section(weakest, "field_percentile") if weakest else None,
        "largest_places_gained": _summary_section(max(gains, key=lambda row: row["rank_change"]), "rank_change")
        if gains else None,
        "largest_places_lost": _summary_section(min(losses, key=lambda row: row["rank_change"]), "rank_change")
        if losses else None,
        "consistency": consistency,
        "trend": trend,
    }


def analyze_runner(event_response: Any, course: Any, split_progresses: Any,
                   raw_results: Any, runner_id: Any) -> Dict[str, Any]:
    """Analyze one completed/incomplete result without I/O or personal inputs.

    Source data failures are represented by unknown sections. A missing runner
    is a caller identity error and raises ``LookupError`` rather than selecting a
    nearby result. The function accepts no weight or demographic arguments.
    """
    event_id, split_names = _event_contract(event_response)
    selected_id = _identifier(runner_id)
    if selected_id is None:
        raise ValueError("runner_id must be an integral identifier")
    results, evidence = _raw_sources(raw_results)
    by_id = _first_results_by_id(results)
    record = by_id.get(selected_id)
    if record is None:
        raise LookupError("runner_id was not found in raw_results")

    finish_index = len(split_names) - 1
    target = _normalise_result(record, selected_id, event_id, len(split_names))
    normalized_by_id = {
        source_id: _normalise_result(row, source_id, event_id, len(split_names))
        for source_id, row in by_id.items()
    }
    all_normalized = list(normalized_by_id.values())
    anonymous_ids = _anonymous_ids(evidence)
    anonymous = selected_id in anonymous_ids
    cohort_key = _age_gender_cohort_key(record, anonymous)
    age_group_normalized = [
        normalized_by_id[source_id]
        for source_id, source_record in by_id.items()
        if source_id not in anonymous_ids
        and cohort_key is not None
        and _age_gender_cohort_key(source_record, False) == cohort_key
    ]
    if target.valid:
        public = _public_identity(record, selected_id, anonymous)
        public.update({
            "status": target.status,
            "finish_seconds": target.finish_seconds,
            "finish_place": target.finish_place,
            "last_recorded_split_index": target.last_index,
            "last_recorded_split_name": split_names[target.last_index] if target.last_index is not None else None,
        })
    else:
        public = {
            "id": selected_id,
            "name": "Anonymous Participant" if anonymous else "Unknown Runner",
            "bib": None,
            "age": None,
            "gender": None,
            "age_group": None,
            "age_group_place": None,
            "gender_place": None,
            "status": "INCOMPLETE",
            "finish_seconds": None,
            "finish_place": None,
            "last_recorded_split_index": None,
            "last_recorded_split_name": None,
        }

    geometry = _prepare_geometry(event_id, course, split_progresses, len(split_names))
    sections = []
    for end_index in range(1, len(split_names)):
        start_index = end_index - 1
        row = {
            "start_index": start_index,
            "start_name": split_names[start_index],
            "end_index": end_index,
            "end_name": split_names[end_index],
            "status": "unknown",
            **_UNKNOWN_SECTION_FIELDS,
        }
        duration = _recorded_duration(target, start_index, end_index) if target.valid else None
        if duration is not None:
            row["status"] = "recorded"
            row["segment_seconds"] = duration
            row["cumulative_seconds"] = target.times[end_index]
            row["cumulative_place"] = target.cumulative_places.get(end_index)
            row["segment_place"] = target.segment_places.get(end_index)
            start_place = target.cumulative_places.get(start_index)
            end_place = target.cumulative_places.get(end_index)
            if start_place is not None and end_place is not None:
                row["rank_change"] = start_place - end_place
            values = _field_values(all_normalized, start_index, end_index)
            if values:
                row["field_count"] = len(values)
                row["field_median_seconds"] = float(statistics.median(values))
                row["field_percentile"] = _field_percentile(values, duration)
            age_group_values = _field_values(age_group_normalized, start_index, end_index)
            if age_group_values:
                row["age_group_count"] = len(age_group_values)
                row["age_group_median_seconds"] = float(statistics.median(age_group_values))
                row["age_group_percentile"] = _field_percentile(age_group_values, duration)
            if geometry is not None:
                terrain = _terrain_section(geometry, start_index, end_index, duration)
                if terrain is not None:
                    row.update(terrain)
        sections.append(row)

    recorded = [row for row in sections if row["status"] == "recorded"]
    terrain_rows = [row for row in recorded if row["terrain_adjusted_effort_m"] is not None]
    complete = bool(target.valid and target.status == "FINISHED" and len(recorded) == finish_index)
    analysis_status = "complete" if complete else "partial" if recorded else "unavailable"
    energy_total = (sum(row["active_energy_kcal_per_kg"] for row in terrain_rows)
                    if terrain_rows else None)
    totals = {
        "recorded_section_count": len(recorded),
        "segment_seconds": sum(row["segment_seconds"] for row in recorded) if recorded else None,
        "distance_m": sum(row["distance_m"] for row in terrain_rows) if terrain_rows else None,
        "gain_m": sum(row["gain_m"] for row in terrain_rows) if terrain_rows else None,
        "loss_m": sum(row["loss_m"] for row in terrain_rows) if terrain_rows else None,
        "terrain_adjusted_effort_m": sum(row["terrain_adjusted_effort_m"] for row in terrain_rows)
        if terrain_rows else None,
        "active_energy_kcal_per_kg": energy_total,
        "active_energy_communication_range_kcal_per_kg": {
            "lower": energy_total * (1 - ENERGY_RANGE_FRACTION),
            "upper": energy_total * (1 + ENERGY_RANGE_FRACTION),
        } if energy_total is not None else None,
    }
    has_field_comparison = any(
        row["field_count"] is not None and row["field_count"] >= 2 for row in recorded
    )
    has_age_group_comparison = any(
        row["age_group_count"] is not None and row["age_group_count"] >= 2 for row in recorded
    )
    terrain_available = bool(geometry is not None and terrain_rows)
    capabilities = {
        "splits": bool(recorded),
        "field_comparison": has_field_comparison,
        "age_group_comparison": has_age_group_comparison,
        "terrain": terrain_available,
        "active_energy": terrain_available and energy_total is not None,
    }
    limitations = [
        "Field comparisons use only valid supplied same-section durations and expose no peer rows.",
        "Age-and-gender comparisons use the source-published category and expose only aggregate counts and percentiles.",
        "Field percentiles are descriptive midranks within the supplied cohort, not population estimates.",
        "The analysis is descriptive and makes no training, medical, or causal claims.",
    ]
    if not target.valid:
        limitations.append("Target split history is unavailable or malformed; no section timing was inferred.")
    elif len(recorded) < finish_index:
        limitations.append("Missing adjacent split reads remain unknown; elapsed gaps and Finish were not inferred.")
    if not terrain_available:
        limitations.append("Terrain and active energy require event-matched, complete, sufficiently dense elevation geometry and strict split progress.")
    else:
        limitations.append(
            "Active energy is bounded graded transport energy per kg only; the +/-25% range is non-statistical and excludes resting energy."
        )

    return {
        "event_id": event_id,
        "runner": public,
        "analysis_status": analysis_status,
        "sections": sections,
        "totals": totals,
        "field_percentile_definition": FIELD_PERCENTILE_DEFINITION,
        "active_energy_range_definition": ACTIVE_ENERGY_RANGE_DEFINITION,
        "summary": _descriptive_summary(sections),
        "capabilities": capabilities,
        "limitations": limitations,
    }
