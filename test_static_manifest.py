from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_static


class StaticManifestTests(unittest.TestCase):
    public_files = {
        "index.html",
        "styles.css",
        "app.js",
        "config.js",
        "race-logic.js",
        "elevation-profile.js",
        "favicon.svg",
        "rut-2026-aid-chart.png",
        "course_signal_shell.js",
        "results_model.js",
        "results_view.js",
        "docs/energy-model.md",
    }
    vendor_files = {
        "vendor/leaflet/leaflet.css",
        "vendor/leaflet/LICENSE",
        "vendor/leaflet/leaflet.js",
    }
    declared_assets = public_files | vendor_files

    @staticmethod
    def write_source(root: Path, relative_path: str) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative_path}\n", encoding="utf-8")

    def write_manifest(self, root: Path, *, omit: set[str] | None = None) -> None:
        for relative_path in self.declared_assets - (omit or set()):
            self.write_source(root, relative_path)

    def build_from(self, root: Path, site_dir: Path) -> Path:
        payload = {"events": []}
        with (
            mock.patch.object(build_static, "ROOT", root),
            mock.patch.object(build_static, "SITE_DIR", site_dir),
            mock.patch.object(build_static.server, "build_payload", return_value=payload) as build_payload,
            mock.patch.object(build_static, "validate_snapshot"),
        ):
            result = build_static.build()
        build_payload.assert_called_once_with(include_courses=True)
        return result

    def assert_vendor_asset_rejected(
        self,
        relative_path: str,
        invalid_kind: str,
        expected_exception: type[BaseException],
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            self.write_manifest(root, omit={relative_path})
            invalid_path = root / relative_path

            if invalid_kind == "directory":
                invalid_path.mkdir(parents=True)
            elif invalid_kind == "symlink":
                target = root / "symlink-target.txt"
                target.write_text("not a declared source\n", encoding="utf-8")
                invalid_path.parent.mkdir(parents=True, exist_ok=True)
                invalid_path.symlink_to(target)
            elif invalid_kind != "missing":
                self.fail(f"Unknown invalid fixture kind: {invalid_kind}")

            site_dir.mkdir(parents=True)
            previous_artifact = site_dir / "previous-artifact.txt"
            previous_artifact.write_text("keep this output\n", encoding="utf-8")
            payload = {"events": []}
            with (
                mock.patch.object(build_static, "ROOT", root),
                mock.patch.object(build_static, "SITE_DIR", site_dir),
                mock.patch.object(build_static.server, "build_payload", return_value=payload) as build_payload,
                mock.patch.object(build_static, "validate_snapshot"),
            ):
                with self.assertRaisesRegex(expected_exception, relative_path):
                    build_static.build()

            build_payload.assert_not_called()
            self.assertEqual(previous_artifact.read_text(encoding="utf-8"), "keep this output\n")
            self.assertEqual(
                {path.relative_to(site_dir).as_posix() for path in site_dir.rglob("*")},
                {"previous-artifact.txt"},
            )

    def assert_symlinked_ancestor_rejected(self, ancestor_path: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            prefix = f"{ancestor_path}/"
            nested_assets = {
                relative_path
                for relative_path in self.declared_assets
                if relative_path.startswith(prefix)
            }
            self.assertTrue(nested_assets)
            self.write_manifest(root, omit=nested_assets)

            external_source = Path(temp_dir) / "external-source"
            for relative_path in nested_assets:
                nested_path = Path(relative_path).relative_to(ancestor_path)
                self.write_source(external_source, nested_path.as_posix())

            ancestor = root / ancestor_path
            ancestor.parent.mkdir(parents=True, exist_ok=True)
            ancestor.symlink_to(external_source, target_is_directory=True)

            site_dir.mkdir(parents=True)
            previous_artifact = site_dir / "previous-artifact.txt"
            previous_artifact.write_text("keep this output\n", encoding="utf-8")
            payload = {"events": []}
            with (
                mock.patch.object(build_static, "ROOT", root),
                mock.patch.object(build_static, "SITE_DIR", site_dir),
                mock.patch.object(build_static.server, "build_payload", return_value=payload) as build_payload,
                mock.patch.object(build_static, "validate_snapshot"),
            ):
                with self.assertRaisesRegex(RuntimeError, rf"symlink.*{ancestor_path}"):
                    build_static.build()

            build_payload.assert_not_called()
            self.assertEqual(previous_artifact.read_text(encoding="utf-8"), "keep this output\n")
            self.assertEqual(
                {path.relative_to(site_dir).as_posix() for path in site_dir.rglob("*")},
                {"previous-artifact.txt"},
            )

    def test_public_manifest_groups_exact_leaflet_assets(self):
        self.assertEqual(len(build_static.PUBLIC_FILES), 12)
        self.assertEqual(set(build_static.PUBLIC_FILES), self.public_files)
        self.assertEqual(set(build_static.PUBLIC_DIRS), {"vendor"})
        self.assertEqual(
            {
                f"{directory}/{relative_path}"
                for directory, relative_paths in build_static.PUBLIC_DIRS.items()
                for relative_path in relative_paths
            },
            self.vendor_files,
        )

    def test_build_emits_only_declared_public_assets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            self.write_manifest(root)

            excluded_sources = {
                "participants/raw-participant.json",
                "task_plan.md",
                ".env",
                "server.py",
                "test_server.py",
                "docs/privacy.md",
                "docs/decisions/005-private-plan.md",
                "vendor/.env",
                "vendor/capture.har",
                "vendor/raw-result-payload.json",
                "vendor/map-library.js",
                "vendor/planning/task_plan.md",
                "vendor/test/test_manifest.py",
                "vendor/source/raw-participant.json",
            }
            for relative_path in excluded_sources:
                self.write_source(root, relative_path)
            (root / "vendor" / "unused-empty-dir").mkdir(parents=True)

            result = self.build_from(root, site_dir)

            emitted_files = {
                path.relative_to(site_dir).as_posix()
                for path in site_dir.rglob("*")
                if path.is_file()
            }
            emitted_directories = {
                path.relative_to(site_dir).as_posix()
                for path in site_dir.rglob("*")
                if path.is_dir()
            }
            expected_files = self.declared_assets | {
                ".nojekyll",
                "data/bootstrap.json",
                "data/live.json",
            }
            self.assertEqual(result, site_dir)
            self.assertEqual(len(emitted_files), 18)
            self.assertEqual(emitted_files, expected_files)
            self.assertEqual(emitted_directories, {"data", "docs", "vendor", "vendor/leaflet"})
            self.assertTrue(excluded_sources.isdisjoint(emitted_files))

    def test_missing_declared_nested_source_fails_fast(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            self.write_manifest(root, omit={"docs/energy-model.md"})

            with self.assertRaisesRegex(FileNotFoundError, "docs/energy-model.md"):
                self.build_from(root, site_dir)

    def test_missing_declared_vendor_asset_fails_fast(self):
        self.assert_vendor_asset_rejected(
            "vendor/leaflet/leaflet.js",
            "missing",
            FileNotFoundError,
        )

    def test_wrong_type_declared_vendor_asset_fails_fast(self):
        self.assert_vendor_asset_rejected(
            "vendor/leaflet/LICENSE",
            "directory",
            RuntimeError,
        )

    def test_symlink_declared_vendor_asset_fails_fast(self):
        self.assert_vendor_asset_rejected(
            "vendor/leaflet/leaflet.css",
            "symlink",
            RuntimeError,
        )

    def test_symlinked_declared_asset_ancestors_fail_fast(self):
        for ancestor_path in ("docs", "vendor", "vendor/leaflet"):
            with self.subTest(ancestor_path=ancestor_path):
                self.assert_symlinked_ancestor_rejected(ancestor_path)

    def test_manifest_traversal_is_rejected_before_payload_or_output_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            (root / "safe").mkdir(parents=True)
            (root / ".env").write_text("TRAVERSAL_SECRET\n", encoding="utf-8")
            site_dir.mkdir()
            previous = site_dir / "previous-artifact.txt"
            previous.write_text("known good\n", encoding="utf-8")

            with (
                mock.patch.object(build_static, "ROOT", root),
                mock.patch.object(build_static, "SITE_DIR", site_dir),
                mock.patch.object(build_static, "PUBLIC_FILES", ("safe/../.env",)),
                mock.patch.object(build_static, "PUBLIC_DIRS", {}),
                mock.patch.object(build_static.server, "build_payload") as build_payload,
            ):
                with self.assertRaisesRegex(RuntimeError, "canonical|traversal"):
                    build_static.build()

            build_payload.assert_not_called()
            self.assertEqual(previous.read_text(encoding="utf-8"), "known good\n")
            self.assertFalse((site_dir / ".env").exists())

    def test_hardlinked_public_source_is_rejected_before_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            self.write_manifest(root, omit={"index.html"})
            private = root / ".env"
            private.write_text("STATIC_HARDLINK_SECRET\n", encoding="utf-8")
            os.link(private, root / "index.html")
            site_dir.mkdir()
            previous = site_dir / "previous-artifact.txt"
            previous.write_text("known good\n", encoding="utf-8")

            with (
                mock.patch.object(build_static, "ROOT", root),
                mock.patch.object(build_static, "SITE_DIR", site_dir),
                mock.patch.object(build_static.server, "build_payload") as build_payload,
            ):
                with self.assertRaisesRegex(RuntimeError, "hardlink|link count"):
                    build_static.build()

            build_payload.assert_not_called()
            self.assertEqual(previous.read_text(encoding="utf-8"), "known good\n")

    def test_sources_are_descriptor_captured_before_payload_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            self.write_manifest(root)
            original = (root / "index.html").read_bytes()
            private = root / ".env"
            private.write_text("STATIC_RACE_SECRET\n", encoding="utf-8")

            def replace_after_validation(*, include_courses):
                self.assertTrue(include_courses)
                (root / "index.html").unlink()
                (root / "index.html").symlink_to(private)
                return {"events": []}

            with (
                mock.patch.object(build_static, "ROOT", root),
                mock.patch.object(build_static, "SITE_DIR", site_dir),
                mock.patch.object(build_static.server, "build_payload", side_effect=replace_after_validation),
                mock.patch.object(build_static, "validate_snapshot"),
            ):
                build_static.build()

            published = (site_dir / "index.html").read_bytes()
            self.assertEqual(published, original)
            self.assertNotIn(b"STATIC_RACE_SECRET", published)

    def test_generated_json_is_projected_through_an_exact_public_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            self.write_manifest(root)
            events = []
            for event_id in build_static.server.EVENT_LABELS:
                events.append({
                    "id": event_id,
                    "label": "Public race",
                    "course": {
                        "track_points": [{"lat": 1, "lng": 2}, {"lat": 3, "lng": 4}],
                        "raw_course_payload": "PRIVATE_COURSE_SENTINEL",
                    },
                    "runners": [{
                        "id": 1,
                        "name": "Public Runner",
                        "email": "PRIVATE_EMAIL_SENTINEL",
                        "raw_participant_payload": {"secret": "PRIVATE_RUNNER_SENTINEL"},
                    }],
                    "positions": [{
                        "id": 1,
                        "lat": 1,
                        "lng": 2,
                        "phone": "PRIVATE_PHONE_SENTINEL",
                    }],
                    "raw_results_payload": "PRIVATE_EVENT_SENTINEL",
                })
            payload = {
                "app": "Course Signal",
                "version": "test",
                "generated_at": "2026-09-14T00:00:00Z",
                "summary": {"errors": 0, "upstream_stale": False, "private": "PRIVATE_SUMMARY_SENTINEL"},
                "events": events,
                "raw_participant_payload": "PRIVATE_TOP_LEVEL_SENTINEL",
            }

            with (
                mock.patch.object(build_static, "ROOT", root),
                mock.patch.object(build_static, "SITE_DIR", site_dir),
                mock.patch.object(build_static.server, "build_payload", return_value=payload),
            ):
                build_static.build()

            for relative in ("data/bootstrap.json", "data/live.json"):
                published = (site_dir / relative).read_text(encoding="utf-8")
                self.assertNotIn("PRIVATE_", published)
                decoded = json.loads(published)
                self.assertEqual(decoded["events"][0]["runners"][0], {
                    "id": 1,
                    "name": "Public Runner",
                })
                self.assertEqual(decoded["events"][0]["positions"][0], {
                    "id": 1,
                    "lat": 1,
                    "lng": 2,
                })

    def test_failed_staging_write_preserves_the_known_good_site(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            site_dir = Path(temp_dir) / "site"
            self.write_manifest(root)
            site_dir.mkdir()
            previous = site_dir / "previous-artifact.txt"
            previous.write_text("known good\n", encoding="utf-8")
            writes = 0

            def fail_second_write(path, body):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("synthetic staging failure")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)

            with (
                mock.patch.object(build_static, "ROOT", root),
                mock.patch.object(build_static, "SITE_DIR", site_dir),
                mock.patch.object(build_static.server, "build_payload", return_value={"events": []}),
                mock.patch.object(build_static, "validate_snapshot"),
                mock.patch.object(build_static, "_write_artifact_file", create=True,
                                  side_effect=fail_second_write),
            ):
                with self.assertRaisesRegex(OSError, "synthetic staging failure"):
                    build_static.build()

            self.assertGreaterEqual(writes, 2)
            self.assertEqual(previous.read_text(encoding="utf-8"), "known good\n")
            self.assertEqual(
                {path.name for path in Path(temp_dir).iterdir()},
                {"source", "site"},
            )


if __name__ == "__main__":
    unittest.main()
