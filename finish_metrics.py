"""Conservative along-route metrics, independent of the network and UI.

GPS matching uses a local metric projection onto line *segments*, restricted to
an observed checkpoint interval. A fix farther than 75 m from that interval or
with near-equal matches over 100 m apart along it is not confidently located.
These are conservative display tolerances, not navigation/safety guarantees.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
from functools import lru_cache
from typing import Any

EARTH_RADIUS_M = 6_371_000
MAX_MATCH_DISTANCE_M = 75.0
AMBIGUITY_ERROR_M = 20.0
AMBIGUITY_ALONG_M = 100.0


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def coordinate(lat: Any, lng: Any) -> tuple[float, float] | None:
    lat, lng = finite_number(lat), finite_number(lng)
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None  # A timezone must be explicit; never assume a fresh UTC fix.
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        return None


def timestamp(value: dt.datetime | None) -> str | None:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def after_seconds(start: dt.datetime | None, seconds: float | None) -> dt.datetime | None:
    if start is None or seconds is None or not math.isfinite(seconds) or seconds < 0:
        return None
    try:
        return start + dt.timedelta(seconds=seconds)
    except OverflowError:
        return None


def great_circle_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat, dlng = lat2 - lat1, math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(min(1.0, math.sqrt(h)))


@dataclasses.dataclass(frozen=True)
class Route:
    points: tuple[tuple[float, float], ...]
    cumulative: tuple[float, ...]
    total: float
    xy: tuple[tuple[float, float], ...]
    longitude_scale: float


@lru_cache(maxsize=16)
def prepare_route(points: tuple[tuple[float, float], ...]) -> Route:
    """Cache geometry by coordinates, so a changed course invalidates itself."""
    cumulative = [0.0] if points else []
    for left, right in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + great_circle_m(left, right))
    scale = math.cos(math.radians(sum(p[0] for p in points) / len(points))) if points else 1.0
    xy = tuple((math.radians(lng) * EARTH_RADIUS_M * scale, math.radians(lat) * EARTH_RADIUS_M)
               for lat, lng in points)
    return Route(points, tuple(cumulative), cumulative[-1] if cumulative else 0.0, xy, scale)


def match_route(point: tuple[float, float], route: Route, lower: float, upper: float,
                accuracy_m: float | None = None) -> tuple[float | None, str | None]:
    """Return along-route metres or a reason; never substitute zero on failure."""
    if route.total <= 0 or not 0 <= lower < upper <= 1:
        return None, "ROUTE_UNAVAILABLE"
    if accuracy_m is not None and (accuracy_m < 0 or accuracy_m > MAX_MATCH_DISTANCE_M):
        return None, "GPS_INACCURATE"
    # Global/polar/antimeridian routes need a different projection. Fail closed.
    if abs(route.longitude_scale) < .01 or any(abs(a[1] - b[1]) > 180 for a, b in zip(route.points, route.points[1:])):
        return None, "ROUTE_UNAVAILABLE"
    x = math.radians(point[1]) * EARTH_RADIUS_M * route.longitude_scale
    y = math.radians(point[0]) * EARTH_RADIUS_M
    low_m, high_m = lower * route.total, upper * route.total
    candidates = []
    for i, ((ax, ay), (bx, by)) in enumerate(zip(route.xy, route.xy[1:])):
        start, end = route.cumulative[i], route.cumulative[i + 1]
        if end < low_m or start > high_m or end <= start:
            continue
        dx, dy = bx - ax, by - ay
        square = dx * dx + dy * dy
        if square <= 0:
            continue
        low_t, high_t = max(0.0, (low_m - start) / (end - start)), min(1.0, (high_m - start) / (end - start))
        fraction = max(low_t, min(high_t, ((x - ax) * dx + (y - ay) * dy) / square))
        error = math.hypot(x - ax - fraction * dx, y - ay - fraction * dy)
        candidates.append((error, start + fraction * (end - start)))
    if not candidates:
        return None, "ROUTE_UNAVAILABLE"
    best_error, along = min(candidates)
    if best_error > MAX_MATCH_DISTANCE_M:
        return None, "GPS_OFF_ROUTE"
    tolerance = max(AMBIGUITY_ERROR_M, accuracy_m or 0)
    plausible = [distance for error, distance in candidates if error <= min(MAX_MATCH_DISTANCE_M, best_error + tolerance)]
    if max(plausible) - min(plausible) > AMBIGUITY_ALONG_M:
        return None, "GPS_AMBIGUOUS"
    return along, None
