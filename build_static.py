#!/usr/bin/env python3
"""Build the current sanitized GitHub Pages artifact."""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any

import server

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "_site"
PUBLIC_FILES = (
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
)
PUBLIC_DIRS = {
    "vendor": (
        "leaflet/leaflet.css",
        "leaflet/LICENSE",
        "leaflet/leaflet.js",
    ),
}
TOP_LEVEL_SCALARS = frozenset({"app", "version", "generated_at", "poll_after_seconds"})
SUMMARY_SCALARS = frozenset({
    "live_gps", "stale_gps", "estimated", "positions", "upstream_stale", "errors",
})
EVENT_SCALARS = frozenset({
    "id", "label", "display_name", "event_date", "start_time", "start_at", "timezone",
    "course_status", "distance_miles", "route_length_m", "color", "upstream_stale",
})
RUNNER_SCALARS = frozenset({
    "id", "bib", "name", "is_anonymous", "status", "last_split_index", "last_split_time",
    "chip_start_seconds", "estimated_finish_seconds", "goal_time_seconds", "overall_place",
    "has_gps", "checkpoint_passages_status", "checkpoint_passages_stale", "event_id", "course",
})
POSITION_SCALARS = RUNNER_SCALARS | frozenset({
    "remaining_m", "distance_source", "rank_eligible", "rank_exclusion", "eta_at", "eta_basis",
    "observation_at", "checkpoint_forecast_basis", "progress", "last_checkpoint", "estimate_model",
    "estimate_basis", "pace_basis", "pace_segments_used", "pace_window_seconds",
    "checkpoint_history_status", "lat", "lng", "source", "freshness", "recorded_at",
    "age_seconds", "accuracy_m", "projected_finish_seconds", "estimate_overdue", "estimate_held",
    "next_checkpoint_at",
})
PASSAGE_SCALARS = frozenset({"split_index", "elapsed_seconds", "passed_at"})
FORECAST_SCALARS = frozenset({"split_index", "estimated_at"})
COURSE_POINT_SCALARS = frozenset({"lat", "lng", "ele"})
SPLIT_POINT_SCALARS = frozenset({"lat", "lng", "name"})
SOURCE_STATE_SCALARS = frozenset({"fetched_at", "stale", "error", "ttl_seconds"})
SOURCE_NAMES = ("event", "course", "gps", "leaderboard")
CAPABILITY_NAMES = frozenset({
    "results", "intermediate_splits", "course_map", "elevation", "live_tracking", "gps",
    "running_eligible",
})
ESTIMATOR_SCALARS = frozenset({
    "model", "terrain_ready", "enabled", "history_status", "history_fetched_at", "history_error",
    "historical_baseline", "elevation_gain_m", "elevation_loss_m",
})


def _json_scalar(value: Any) -> bool:
    return (value is None or isinstance(value, (bool, int, str))
            or isinstance(value, float) and math.isfinite(value))


def _project_scalars(value: Any, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {field: value[field] for field in fields if field in value and _json_scalar(value[field])}


def _project_object_list(value: Any, projector) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [projector(item) for item in value if isinstance(item, dict)]


def _project_runner(value: Any) -> dict[str, Any]:
    clean = _project_scalars(value, RUNNER_SCALARS)
    if isinstance(value, dict) and "checkpoint_passages" in value:
        clean["checkpoint_passages"] = _project_object_list(
            value.get("checkpoint_passages"),
            lambda row: _project_scalars(row, PASSAGE_SCALARS),
        )
    return clean


def _project_position(value: Any) -> dict[str, Any]:
    clean = _project_scalars(value, POSITION_SCALARS)
    if isinstance(value, dict) and "checkpoint_passages" in value:
        clean["checkpoint_passages"] = _project_object_list(
            value.get("checkpoint_passages"),
            lambda row: _project_scalars(row, PASSAGE_SCALARS),
        )
    if isinstance(value, dict) and "checkpoint_forecasts" in value:
        clean["checkpoint_forecasts"] = _project_object_list(
            value.get("checkpoint_forecasts"),
            lambda row: _project_scalars(row, FORECAST_SCALARS),
        )
    return clean


def _project_event(value: Any) -> dict[str, Any]:
    clean = _project_scalars(value, EVENT_SCALARS)
    if not isinstance(value, dict):
        return clean
    split_names = value.get("split_names")
    clean["split_names"] = [name for name in split_names if isinstance(name, str)] if isinstance(split_names, list) else []
    clean["runners"] = _project_object_list(value.get("runners"), _project_runner)
    clean["positions"] = _project_object_list(value.get("positions"), _project_position)
    errors = value.get("errors")
    clean["errors"] = [error for error in errors if isinstance(error, str)] if isinstance(errors, list) else []
    source_freshness = value.get("source_freshness")
    clean["source_freshness"] = {
        source: _project_scalars(source_freshness[source], SOURCE_STATE_SCALARS)
        for source in SOURCE_NAMES
        if isinstance(source_freshness, dict) and isinstance(source_freshness.get(source), dict)
    }
    clean["capabilities"] = _project_scalars(value.get("capabilities"), CAPABILITY_NAMES)
    clean["estimator"] = _project_scalars(value.get("estimator"), ESTIMATOR_SCALARS)
    course = value.get("course")
    if isinstance(course, dict):
        progresses = course.get("progress_points")
        clean["course"] = {
            "track_points": _project_object_list(
                course.get("track_points"), lambda point: _project_scalars(point, COURSE_POINT_SCALARS)
            ),
            "split_points": _project_object_list(
                course.get("split_points"), lambda point: _project_scalars(point, SPLIT_POINT_SCALARS)
            ),
            "progress_points": [point for point in progresses if _json_scalar(point)]
            if isinstance(progresses, list) else [],
        }
    return clean


def sanitize_public(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a snapshot through the exact recursively allowlisted public schema."""
    clean = _project_scalars(payload, TOP_LEVEL_SCALARS)
    clean["summary"] = _project_scalars(
        payload.get("summary") if isinstance(payload, dict) else None,
        SUMMARY_SCALARS,
    )
    events = payload.get("events") if isinstance(payload, dict) else None
    clean["events"] = _project_object_list(events, _project_event)
    clean["delivery"] = "periodic_snapshot"
    clean["refresh_expected_seconds"] = 300
    return clean


def make_live_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Make the smaller refresh payload while retaining the full roster."""
    live = copy.deepcopy(payload)
    for event in live.get("events") or []:
        event.pop("course", None)
    return live


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _write_artifact_file(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_artifact_file(path, _json_bytes(payload))


def _manifest_paths() -> tuple[Path, ...]:
    names: list[Any] = list(PUBLIC_FILES)
    for directory, relative_names in PUBLIC_DIRS.items():
        names.extend(f"{directory}/{name}" for name in relative_names)
    normalized: list[Path] = []
    seen: set[str] = set()
    for name in names:
        if (not isinstance(name, str) or not name or "\\" in name or "\x00" in name
                or name.startswith("/") or "//" in name):
            raise RuntimeError(f"Declared public asset path must be canonical: {name!r}")
        path = Path(name)
        if path.as_posix() != name or any(part in {"", ".", ".."} for part in path.parts):
            raise RuntimeError(f"Declared public asset path contains traversal or is not canonical: {name!r}")
        if name in seen:
            raise RuntimeError(f"Duplicate declared public asset path: {name}")
        seen.add(name)
        normalized.append(path)
    return tuple(normalized)


def _read_declared_asset(root_fd: int, relative_path: Path) -> bytes:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.dup(root_fd)
    file_fd = None
    try:
        relative_ancestor = Path()
        for part in relative_path.parts[:-1]:
            relative_ancestor /= part
            try:
                next_fd = os.open(
                    part, directory_flags | close_on_exec, dir_fd=directory_fd,
                )
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Missing declared public asset: {relative_path}"
                ) from None
            except OSError:
                raise RuntimeError(
                    "Declared public asset ancestor must not be a symlink and must be a directory: "
                    f"{relative_ancestor} (for {relative_path})"
                ) from None
            os.close(directory_fd)
            directory_fd = next_fd
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                raise RuntimeError(
                    "Declared public asset ancestor must be a directory: "
                    f"{relative_ancestor} (for {relative_path})"
                )

        try:
            file_fd = os.open(
                relative_path.parts[-1], file_flags | close_on_exec, dir_fd=directory_fd,
            )
        except FileNotFoundError:
            raise FileNotFoundError(f"Missing declared public asset: {relative_path}") from None
        except OSError:
            raise RuntimeError(
                f"Declared public asset must not be a symlink: {relative_path}"
            ) from None
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"Declared public asset must be a regular file: {relative_path}")
        if before.st_nlink != 1:
            raise RuntimeError(
                f"Declared public asset must not be a hardlink (link count must be one): {relative_path}"
            )
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
            raise RuntimeError(f"Declared public asset changed while being read: {relative_path}")
        return body
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        try:
            os.close(directory_fd)
        except OSError:
            pass


def capture_public_assets() -> tuple[tuple[Path, bytes], ...]:
    """Validate and descriptor-capture every exact manifest entry."""
    assets = _manifest_paths()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(ROOT, flags | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise RuntimeError("Public asset root must not be a symlink and must be a directory") from None
    try:
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise RuntimeError("Public asset root must be a directory")
        return tuple((path, _read_declared_asset(root_fd, path)) for path in assets)
    finally:
        os.close(root_fd)


def validate_public_assets() -> tuple[Path, ...]:
    """Compatibility validation entry point used by focused manifest checks."""
    return tuple(path for path, _body in capture_public_assets())


def validate_snapshot(payload: dict[str, Any]) -> None:
    events = payload.get("events") or []
    if {e.get("id") for e in events} != set(server.EVENT_LABELS):
        raise RuntimeError("Snapshot missing expected races; do not replace published data")
    if payload.get("summary", {}).get("errors") or payload.get("summary", {}).get("upstream_stale"):
        raise RuntimeError("Incomplete/stale source; do not publish a new snapshot")
    if any(len(e.get("course", {}).get("track_points", [])) < 2 or not e.get("runners") for e in events):
        raise RuntimeError("Snapshot missing course or roster; do not publish")


def _validate_output_target() -> None:
    try:
        parent_mode = SITE_DIR.parent.lstat().st_mode
    except OSError:
        raise RuntimeError("Static output parent must exist and be a real directory") from None
    if stat.S_ISLNK(parent_mode) or not stat.S_ISDIR(parent_mode):
        raise RuntimeError("Static output parent must not be a symlink and must be a directory")
    try:
        output_mode = SITE_DIR.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(output_mode) or not stat.S_ISDIR(output_mode):
        raise RuntimeError("Static output target must not be a symlink and must be a directory")


def _install_staged_site(staging: Path) -> None:
    backup = None
    try:
        SITE_DIR.lstat()
    except FileNotFoundError:
        pass
    else:
        backup = SITE_DIR.parent / f".{SITE_DIR.name}.backup-{uuid.uuid4().hex}"
        os.replace(SITE_DIR, backup)
    try:
        os.replace(staging, SITE_DIR)
    except BaseException:
        if backup is not None:
            os.replace(backup, SITE_DIR)
        raise
    if backup is not None:
        shutil.rmtree(backup)


def build() -> Path:
    public_assets = capture_public_assets()
    _validate_output_target()
    payload = sanitize_public(server.build_payload(include_courses=True))
    validate_snapshot(payload)

    staging = Path(tempfile.mkdtemp(
        prefix=f".{SITE_DIR.name}.staging-", dir=SITE_DIR.parent,
    ))
    try:
        for relative_path, body in public_assets:
            _write_artifact_file(staging / relative_path, body)
        write_json(staging / "data" / "bootstrap.json", payload)
        write_json(staging / "data" / "live.json", make_live_payload(payload))
        _write_artifact_file(staging / ".nojekyll", b"")
        _install_staged_site(staging)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return SITE_DIR


def main() -> int:
    site = build()
    bootstrap = json.loads((site / "data" / "bootstrap.json").read_text(encoding="utf-8"))
    print(json.dumps({
        "site": str(site),
        "generated_at": bootstrap.get("generated_at"),
        "events": len(bootstrap.get("events") or []),
        "runners": sum(len(event.get("runners") or []) for event in bootstrap.get("events") or []),
        "positions": bootstrap.get("summary", {}).get("positions", 0),
        "errors": bootstrap.get("summary", {}).get("errors", 0),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
