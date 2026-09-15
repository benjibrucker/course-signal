"""Pure, conservative terrain/checkpoint pilot; all times are chip seconds.

Minetti et al. (2002) running cost polynomial (grade is a fraction):
https://iris.unibs.it/bitstream/11379/540545/1/Minetti%20JAP%202002.pdf
The [1, 6] cost floor/cap and pace clamp are ENGINEERING safeguards, not an
empirical prediction that technical descents are faster than flat running.
Callers must verify elevation units/source and cache the profile per event.
No missing timestamps or elevations are invented here.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
from numbers import Real
from typing import Optional

SAMPLE_METERS = 50.0
SMOOTH_WINDOW_METERS = 150.0
MAX_DATA_GAP_METERS = 500.0
MAX_ROUTE_METERS = 1_000_000.0
MAX_INPUT_POINTS = 1_000_000
MAX_CHIP_SECONDS = 1e12
HALF_LIFE_SECONDS = 90.0 * 60.0


@dataclass(frozen=True)
class TerrainProfile:
    cumulative_m: tuple[float, ...]
    elevation_m: tuple[float, ...]
    cumulative_effort_m: tuple[float, ...]
    gain_m: float
    loss_m: float
    sampled_point_count: int


@dataclass(frozen=True)
class Prediction:
    progress: float
    finish_seconds: float
    overdue: bool
    pace_basis: str
    pace_segments_used: int
    pace_window_seconds: float
    next_checkpoint_seconds: float
    checkpoint_seconds: tuple[tuple[int, float], ...]


def _number(value) -> bool:
    try:
        return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _interpolate(x, y, value):
    if value <= x[0]:
        return y[0]
    if value >= x[-1]:
        return y[-1]
    i = bisect_right(x, value) - 1
    return y[i] + (y[i + 1] - y[i]) * ((value - x[i]) / (x[i + 1] - x[i]))


def build_profile(cumulative_m: list, elevation_m: list) -> Optional[TerrainProfile]:
    """Return distance-smoothed effort, or None for unsafe/incomplete data.

    Distances must start at zero and span 100 m..1000 km. Gaps over 500 m
    are rejected; equal-distance samples must agree within 0.01 m elevation.
    Elevations must be finite, verified meters in [-500, 9000]. Sampling is
    every 50 m plus the exact finish; a centered 150 m piecewise-linear area
    mean smooths elevation by distance, not point density. Exact start/finish
    elevations are preserved. Gain/loss describe this smoothed profile.
    """
    if not isinstance(cumulative_m, (list, tuple)) or not isinstance(elevation_m, (list, tuple)):
        return None
    if not 2 <= len(cumulative_m) <= MAX_INPUT_POINTS or len(cumulative_m) != len(elevation_m):
        return None
    x, z = [], []
    for distance, altitude in zip(cumulative_m, elevation_m):
        if not _number(distance) or not _number(altitude):
            return None
        if not 0 <= distance <= MAX_ROUTE_METERS or not -500 <= altitude <= 9000:
            return None
        distance, altitude = float(distance), float(altitude)
        if x:
            gap = distance - x[-1]
            if gap < 0 or gap > MAX_DATA_GAP_METERS:
                return None
            if gap == 0:
                if abs(altitude - z[-1]) > .01:
                    return None
                continue
        x.append(distance)
        z.append(altitude)
    if len(x) < 2 or x[0] != 0 or x[-1] < 100:
        return None

    # Integral of the source polyline gives density-independent box smoothing.
    areas = [0.0]
    for i in range(1, len(x)):
        areas.append(areas[-1] + (x[i] - x[i - 1]) * (z[i] + z[i - 1]) / 2)

    def integral(value):
        if value >= x[-1]:
            return areas[-1]
        i = bisect_right(x, value) - 1
        delta = value - x[i]
        end_z = z[i] + (z[i + 1] - z[i]) * delta / (x[i + 1] - x[i])
        return areas[i] + delta * (z[i] + end_z) / 2

    samples = [i * SAMPLE_METERS for i in range(int(x[-1] // SAMPLE_METERS) + 1)]
    if samples[-1] != x[-1]:
        samples.append(x[-1])
    heights = [z[0]]
    for distance in samples[1:-1]:
        lo = max(0.0, distance - SMOOTH_WINDOW_METERS / 2)
        hi = min(x[-1], distance + SMOOTH_WINDOW_METERS / 2)
        heights.append((integral(hi) - integral(lo)) / (hi - lo))
    heights.append(z[-1])
    efforts = [0.0]
    gain = loss = 0.0
    for i in range(1, len(samples)):
        dx = samples[i] - samples[i - 1]
        dz = heights[i] - heights[i - 1]
        grade = max(-.45, min(.45, dz / dx))
        cost = (((((155.4 * grade - 30.4) * grade - 43.3) * grade + 46.3)
                 * grade + 19.5) * grade + 3.6)
        weight = max(1.0, min(6.0, cost / 3.6))
        efforts.append(efforts[-1] + weight * math.hypot(dx, dz))
        gain += max(0.0, dz)
        loss += max(0.0, -dz)
    return TerrainProfile(tuple(samples), tuple(heights), tuple(efforts), gain, loss, len(samples))


def predict(profile: TerrainProfile, progresses: list[float],
            checkpoint_times: list[tuple[int, float]], last_index: int,
            last_time: float, race_clock: float) -> Optional[Prediction]:
    """Project only within the next unobserved checkpoint interval.

    ``progresses`` must strictly increase from 0 to 1. ``checkpoint_times``
    contains (index, chip_elapsed_seconds) observations, starting at (0, 0)
    and ending exactly at (last_index, last_time). Missing checkpoints stay
    missing: an interval spanning them is one observation. ``race_clock`` is
    elapsed CHIP time, never time since the event gun. Start/finish return None.

    Last <=3 observed intervals use effort-distance weights with 90-minute
    recency half-life, measured at each interval's observed endpoint relative
    to the latest observation. Clamp that normalized pace to 0.5..2 times the
    cumulative pace, then blend 70% recent / 30% cumulative. Stops stay in the
    elapsed times. Metadata window is the span of those observed intervals.
    """
    if not isinstance(profile, TerrainProfile):
        return None
    if not isinstance(progresses, (list, tuple)) or len(progresses) < 2:
        return None
    if (not all(_number(v) and 0 <= v <= 1 for v in progresses)
            or progresses[0] != 0 or progresses[-1] != 1
            or any(b <= a for a, b in zip(progresses, progresses[1:]))):
        return None
    if type(last_index) is not int or not 0 < last_index < len(progresses) - 1:
        return None
    if (not _number(last_time) or not _number(race_clock)
            or not 0 < last_time <= race_clock <= MAX_CHIP_SECONDS):
        return None
    if not isinstance(checkpoint_times, (list, tuple)) or len(checkpoint_times) < 2:
        return None
    previous_index, previous_time = -1, -1.0
    for item in checkpoint_times:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        index, seconds = item
        if (type(index) is not int or not previous_index < index <= last_index
                or not _number(seconds) or not previous_time < seconds <= race_clock):
            return None
        previous_index, previous_time = index, seconds
    if (tuple(checkpoint_times[0]) != (0, 0)
            or tuple(checkpoint_times[-1]) != (last_index, last_time)):
        return None

    # Profiles are constructed once by build_profile, not revalidated per runner.
    distances, effort = profile.cumulative_m, profile.cumulative_effort_m
    total_distance, total_effort = distances[-1], effort[-1]
    at = lambda index: _interpolate(distances, effort, progresses[index] * total_distance)
    last_effort = at(last_index)
    next_effort = at(last_index + 1)
    if last_effort <= 0 or next_effort <= last_effort:
        return None
    cumulative_pace = last_time / last_effort
    recent_history = checkpoint_times[-4:]
    weighted_seconds = weighted_effort = 0.0
    for (i, t), (j, u) in zip(recent_history, recent_history[1:]):
        de = at(j) - at(i)
        if de <= 0:
            return None
        recency = math.exp(-math.log(2) * (last_time - u) / HALF_LIFE_SECONDS)
        weighted_seconds += (u - t) * recency
        weighted_effort += de * recency
    recent_pace = weighted_seconds / weighted_effort
    recent_pace = max(.5 * cumulative_pace, min(2 * cumulative_pace, recent_pace))
    pace = .7 * recent_pace + .3 * cumulative_pace
    finish_seconds = last_time + pace * (total_effort - last_effort)
    next_seconds = last_time + pace * (next_effort - last_effort)
    if (not all(math.isfinite(v) and 0 < v <= MAX_CHIP_SECONDS
                for v in (pace, finish_seconds, next_seconds))
            or next_seconds <= last_time or finish_seconds < next_seconds):
        return None
    target_effort = min(next_effort, last_effort + (race_clock - last_time) / pace)
    distance = _interpolate(effort, distances, target_effort)
    lower, upper = progresses[last_index:last_index + 2]
    # A 99% cap can round up to upper for adjacent floats; never cross it.
    cap = min(lower + .99 * (upper - lower), math.nextafter(upper, lower))
    progress = max(lower, min(cap, distance / total_distance))
    count = len(recent_history) - 1
    # Evaluate the SAME fitted pace at each remaining effort boundary. Forecasts
    # never become history and never alter the next-interval location estimate.
    checkpoint_seconds = tuple(
        (i, next_seconds if i == last_index + 1 else finish_seconds if i == len(progresses) - 1
         else last_time + pace * (at(i) - last_effort))
        for i in range(last_index + 1, len(progresses)))
    return Prediction(progress, finish_seconds, race_clock >= next_seconds,
                      'RECENT_SEGMENTS' if count > 1 else 'CHECKPOINT_AVERAGE',
                      count, last_time - recent_history[0][1], next_seconds, checkpoint_seconds)
