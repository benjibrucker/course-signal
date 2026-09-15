#!/usr/bin/env python3
"""Read-only, aggregate withheld-checkpoint evaluation. Never saves runner records."""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import urllib.request
import server
from terrain_model import build_profile, predict


def fetch(path):
    url = server.UPSTREAM + path
    request = urllib.request.Request(url, headers={"Accept": "application/json", "Origin": "https://competitivetiming.com"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def errors_summary(values):
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {"count": len(values), "median_absolute_minutes": round(statistics.median(values) / 60, 2),
            "mean_absolute_minutes": round(statistics.mean(values) / 60, 2),
            "p90_absolute_minutes": round(ordered[min(len(ordered)-1, math.ceil(len(ordered)*.9)-1)] / 60, 2)}


def evaluate(event_id):
    event_response = fetch(f"/events/{event_id}")
    course_response = fetch(f"/course-maps/event/{event_id}?include=points")
    result_response = fetch(f"/events/{event_id}/results")
    maps = [c for c in course_response.get("courseMaps", []) if c.get("eventId") == event_id]
    if len(maps) != 1 or not isinstance(result_response.get("results"), list):
        raise ValueError("Missing or ambiguous public course/results")
    rows = result_response["results"]
    if result_response.get("has_more") is True:
        raise ValueError("Incomplete results response")
    if len({r.get("id") for r in rows}) != len(rows):
        raise ValueError("Duplicate results; evaluate only an unambiguous cohort")
    for field in ("total", "total_count"):
        if field in result_response and result_response[field] != len(rows):
            raise ValueError("Results count disagrees with metadata")
    track = maps[0]["trackPoints"]
    cumulative, total = server.route_distances(track)
    profile = build_profile(cumulative, [p.get("ele") for p in track])
    progresses = server.split_progresses(event_response, maps[0], track, cumulative, total)
    if profile is None or any(p is None for p in progresses):
        raise ValueError("Terrain/checkpoint coverage insufficient for evaluation")
    weights = server.segment_weights(event_response, progresses)
    terrain_errors, baseline_errors, by_checkpoint = [], [], collections.defaultdict(lambda: {"terrain": [], "baseline": []})
    qualified = skipped = 0
    for row in rows:
        if any(row.get(f) for f in ("is_anonymous", "athlete_anonymous", "dns", "dnf", "dropped", "dq", "dnq")):
            continue
        finish = server.as_float(row.get("finish_time_seconds"))
        offset = server.as_float(row.get("chip_start_seconds"))
        if finish is None or finish <= 0 or offset is None or offset < 0:
            continue
        times = {0: 0.0}
        valid = True
        for split in row.get("splits") or []:
            index = server.as_int(split.get("split_index"))
            elapsed = server.as_float(split.get("elapsed_seconds"))
            if index == 0:
                continue  # Upstream split-zero elapsed is the start offset, not chip time.
            if index is None or index in times or elapsed is None or not 0 < index < len(progresses):
                valid = False
                break
            times[index] = elapsed
        observed = sorted(times.items())
        if not valid or times.get(len(progresses)-1) != finish or any(b[1] <= a[1] for a,b in zip(observed, observed[1:])):
            continue
        qualified += 1
        for index, elapsed in observed[1:-1]:
            if index + 1 not in times:
                continue
            # Only prefix observations enter predictions; the next split is withheld.
            prefix = [(i,t) for i,t in observed if i <= index]
            predicted = predict(profile, progresses, prefix, index, elapsed, elapsed)
            remaining_weights = sum(weights[index+1:])
            if predicted is None or not remaining_weights > 0 or not progresses[index] > 0:
                skipped += 1
                continue
            actual = times[index+1]
            finish_guess = elapsed / progresses[index]
            baseline = elapsed + (finish_guess-elapsed) * weights[index+1] / remaining_weights
            t_error = abs(predicted.next_checkpoint_seconds-actual)
            b_error = abs(baseline-actual)
            terrain_errors.append(t_error); baseline_errors.append(b_error)
            by_checkpoint[index+1]["terrain"].append(t_error)
            by_checkpoint[index+1]["baseline"].append(b_error)
    if not terrain_errors:
        raise ValueError("No valid withheld-checkpoint comparisons")
    return {"event_id": event_id, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "model": "terrain-pilot-v1", "source_urls": [server.UPSTREAM + f"/events/{event_id}/results", server.UPSTREAM + f"/events/{event_id}", server.UPSTREAM + f"/course-maps/event/{event_id}?include=points"],
            "qualified_finishers": qualified, "skipped_predictions": skipped,
            "terrain": errors_summary(terrain_errors), "existing_checkpoint_model": errors_summary(baseline_errors),
            "by_next_checkpoint": {str(i): {name: errors_summary(values) for name,values in pairs.items()} for i,pairs in sorted(by_checkpoint.items())},
            "notes": ["Aggregate historical replay; no runner identities or split histories stored.",
                      "Only prefix checkpoints enter each prediction; next checkpoint is withheld.",
                      "Completed, nonflagged, nonanonymous cohort only: not proof of race-day accuracy or dropout behavior.",
                      "Same-event geometry is used for this replay; no prior-year checkpoint remapping or calibration applied."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.event.startswith("the-rut-") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.event):
        parser.error("Use a canonical Rut event slug")
    report = evaluate(args.event)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
