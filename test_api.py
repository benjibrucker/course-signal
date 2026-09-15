import contextlib
import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from api.index import handler, response_for
import build_static


class ApiTests(unittest.TestCase):
    @contextlib.contextmanager
    def local_rut_server(self, root):
        import server

        with patch.object(server, "APP_ROOT", root):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.RutHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{httpd.server_port}"
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()

    @staticmethod
    def local_get(base, target):
        parsed = urllib.parse.urlsplit(base)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
        try:
            connection.request("GET", target)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    @staticmethod
    def write_fixture(root, relative_path, body=None):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = body if body is not None else f"fixture for {relative_path}\n".encode("utf-8")
        path.write_bytes(payload)
        return payload

    @staticmethod
    def declared_static_files():
        return set(build_static.PUBLIC_FILES) | {
            f"{directory}/{relative_path}"
            for directory, relative_paths in build_static.PUBLIC_DIRS.items()
            for relative_path in relative_paths
        }

    def test_local_static_server_serves_results_assets_and_methodology(self):
        expected_types = {
            "results_model.js": "text/javascript",
            "results_view.js": "text/javascript",
            "docs/energy-model.md": "text/markdown",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fixtures = {
                relative_path: self.write_fixture(root, relative_path)
                for relative_path in expected_types
            }
            with self.local_rut_server(root) as base:
                for relative_path, expected_type in expected_types.items():
                    with self.subTest(relative_path=relative_path):
                        status, headers, body = self.local_get(base, f"/{relative_path}")
                        self.assertEqual(status, 200)
                        self.assertEqual(headers["Content-Type"], f"{expected_type}; charset=utf-8")
                        self.assertEqual(headers["Content-Length"], str(len(fixtures[relative_path])))
                        self.assertEqual(body, fixtures[relative_path])
                        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_local_static_server_matches_exact_build_manifest(self):
        import server

        declared = self.declared_static_files()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fixtures = {
                relative_path: self.write_fixture(root, relative_path)
                for relative_path in declared
            }
            with self.local_rut_server(root) as base:
                for relative_path in sorted(declared):
                    with self.subTest(relative_path=relative_path):
                        status, headers, body = self.local_get(base, f"/{relative_path}")
                        self.assertEqual(status, 200)
                        self.assertEqual(headers["Content-Length"], str(len(fixtures[relative_path])))
                        self.assertEqual(body, fixtures[relative_path])
                for relative_path in ("app.js", "course_signal_shell.js", "results_model.js"):
                    with self.subTest(versioned=relative_path):
                        status, _headers, body = self.local_get(base, f"/{relative_path}?v=1.0.0")
                        self.assertEqual(status, 200)
                        self.assertEqual(body, fixtures[relative_path])
        self.assertEqual(set(server.STATIC_FILES), declared)

    def test_local_static_server_serves_each_supported_application_route_query(self):
        import server

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            expected = self.write_fixture(root, "index.html", b"COURSE SIGNAL ROUTE\n")
            with self.local_rut_server(root) as base:
                for event_id, _label in server.EVENTS:
                    for mode in ("live", "results"):
                        target = f"/?race=the-rut&event={event_id}&mode={mode}"
                        with self.subTest(target=target):
                            status, _headers, body = self.local_get(base, target)
                            self.assertEqual((status, body), (200, expected))

                runner_target = "/?race=the-rut&event=the-rut-21k-2026&mode=results&runner=87319813"
                status, _headers, body = self.local_get(base, runner_target)
                self.assertEqual((status, body), (200, expected))

                for target in (
                    "/?race=the-rut&event=the-rut-100k-2026&mode=results",
                    "/?race=other&event=the-rut-21k-2026&mode=results",
                    "/?race=the-rut&event=the-rut-21k-2026&mode=results&runner=01",
                    "/?race=the-rut&event=the-rut-21k-2026&mode=results&weight=180",
                ):
                    with self.subTest(target=target):
                        status, _headers, _body = self.local_get(base, target)
                        self.assertEqual(status, 404)

    def test_local_static_server_rejects_unlisted_and_noncanonical_paths(self):
        excluded = {
            ".env": b"SYNTHETIC_TEST_VALUE=not-a-real-secret\n",
            ".env.local": b"SYNTHETIC_TEST_VALUE=not-a-real-secret\n",
            ".vercel/project.json": b"{}\n",
            "capture.har": b"{}\n",
            "docs/privacy.md": b"private fixture\n",
            "docs/results-api-contract.md": b"private fixture\n",
            "docs/decisions/005-private-plan.md": b"private fixture\n",
            "planning/task_plan.md": b"private fixture\n",
            "server.py": b"# private fixture\n",
            "task_plan.md": b"private fixture\n",
            "test_api.py": b"# private fixture\n",
            "unknown.js": b"/* private fixture */\n",
            "vendor/capture.har": b"{}\n",
            "vendor/leaflet/images/marker-icon.png": b"not a real image\n",
        }
        rejected_targets = (
            "/.env",
            "/.env.local",
            "/.vercel/project.json",
            "/capture.har",
            "/docs/privacy.md",
            "/docs/results-api-contract.md",
            "/docs/decisions/005-private-plan.md",
            "/planning/task_plan.md",
            "/server.py",
            "/task_plan.md",
            "/test_api.py",
            "/unknown.js",
            "/vendor/capture.har",
            "/vendor/leaflet/images/marker-icon.png",
            "/docs/",
            "/vendor/leaflet/",
            "/index.html/",
            "/docs/energy-model.md/",
            "/docs/energy-model.md/index.html",
            "//results_model.js",
            "/docs//energy-model.md",
            "/./app.js",
            "/docs/./energy-model.md",
            "/docs/../app.js",
            "/%2e/app.js",
            "/%2e%2e/server.py",
            "/docs/%2e%2e/server.py",
            "/%252e%252e/server.py",
            "/docs%2fenergy-model.md",
            "/results%5fmodel.js",
            "/app.js?path=/server.py",
            "/app.js?asset=unknown.js",
            "/app.js?",
            "/app.js?v=1.0.0&path=/server.py",
            "/app.js%3fpath=/server.py",
            "/app.js\\ignored",
            "/docs%5cenergy-model.md",
            "/app.js%00",
            "/app.js%0a",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for relative_path in self.declared_static_files():
                self.write_fixture(root, relative_path)
            for relative_path, body in excluded.items():
                self.write_fixture(root, relative_path, body)
            with self.local_rut_server(root) as base:
                for target in rejected_targets:
                    with self.subTest(target=target):
                        status, _headers, _body = self.local_get(base, target)
                        self.assertEqual(status, 404)

    def test_local_static_server_rejects_symlinks_and_nonfiles(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory).resolve()
            root = temporary / "root"
            root.mkdir()
            app_body = self.write_fixture(root, "app.js")
            (root / "results_model.js").symlink_to(root / "app.js")
            (root / "results_view.js").mkdir()
            external_docs = temporary / "external-docs"
            self.write_fixture(external_docs, "energy-model.md")
            (root / "docs").symlink_to(external_docs, target_is_directory=True)

            with self.local_rut_server(root) as base:
                status, _headers, body = self.local_get(base, "/app.js")
                self.assertEqual((status, body), (200, app_body))
                for target in (
                    "/results_model.js",
                    "/results_view.js",
                    "/docs/energy-model.md",
                ):
                    with self.subTest(target=target):
                        status, _headers, _body = self.local_get(base, target)
                        self.assertEqual(status, 404)

    def test_local_static_server_rejects_allowlisted_hardlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            private = root / ".env"
            private.write_bytes(b"STATIC_HARDLINK_SECRET\n")
            os.link(private, root / "app.js")

            with self.local_rut_server(root) as base:
                status, _headers, body = self.local_get(base, "/app.js")

            self.assertEqual(status, 404)
            self.assertNotIn(b"STATIC_HARDLINK_SECRET", body)

    def test_local_static_server_reads_from_the_validated_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            public = self.write_fixture(root, "app.js", b"PUBLIC_APP_BODY\n")
            private = self.write_fixture(root, ".env", b"STATIC_RACE_SECRET\n")
            original_read_bytes = Path.read_bytes
            swapped = False

            def swap_before_path_read(path):
                nonlocal swapped
                if path == root / "app.js" and not swapped:
                    swapped = True
                    path.unlink()
                    path.symlink_to(root / ".env")
                return original_read_bytes(path)

            with (
                patch.object(Path, "read_bytes", swap_before_path_read),
                self.local_rut_server(root) as base,
            ):
                status, _headers, body = self.local_get(base, "/app.js")

            self.assertEqual(status, 200)
            self.assertEqual(body, public)
            self.assertNotEqual(body, private)

    def test_health_and_route_allowlist(self):
        self.assertEqual(response_for('health')[0], 200)
        for path in ['../server.py', 'https://example.com', 'not-found']:
            self.assertEqual(response_for(path)[0], 404)

    def test_upstream_error_is_not_served_as_new_live_data(self):
        with patch('api.index.server.build_payload', return_value={'summary':{'errors':1},'events':[{}]}):
            self.assertEqual(response_for('live')[0],503)
        with patch('api.index.server.build_payload', side_effect=RuntimeError('private diagnostic')):
            status, data=response_for('live')
            self.assertEqual(status,503)
            self.assertNotIn('private diagnostic',json.dumps(data))

    def test_live_delivery_and_minimization(self):
        payload={'summary':{'errors':0},'events':[{'runners':[{'name':'Test','age':33,'city':'Town'}], 'positions':[{'lat':45,'lng':-111,'battery_pct':88}]}]}
        with patch('api.index.server.build_payload',return_value=payload):
            status, data=response_for('live')
        self.assertEqual(status,200)
        self.assertEqual(data['delivery'],'live_api')
        self.assertEqual(data['refresh_expected_seconds'],15)
        self.assertNotIn('age',data['events'][0]['runners'][0])
        self.assertNotIn('battery_pct',data['events'][0]['positions'][0])
        self.assertIn('age',payload['events'][0]['runners'][0])

    def test_http_cors_methods_and_no_file_serving(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{httpd.server_port}'
        try:
            with urllib.request.urlopen(base+'/api/health') as response:
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'],'https://benjibrucker.github.io')
                self.assertEqual(json.load(response)['status'],'ok')
            for path, method, expected in [('/server.py','GET',404),('/api/live','POST',405),('/api/live','DELETE',405)]:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(urllib.request.Request(base+path,method=method))
                self.assertEqual(caught.exception.code,expected)
        finally:
            httpd.shutdown();httpd.server_close();thread.join()


if __name__=='__main__': unittest.main()
