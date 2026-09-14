"""Regression tests for adversarial source content and local trust boundaries."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
import os
import socket
from types import SimpleNamespace
import zipfile

from content_discovery_capture.application import Application
from content_discovery_capture.derivation import derive
from content_discovery_capture.domain import CaptureError, Scope, safe_text, safe_url, stable_url
from content_discovery_capture.interfaces.review import decide
from content_discovery_capture.reporting import create_manifest
from content_discovery_capture.runtimes.local import HttpProvider


class SecurityRegressionTests(TestCase):
    def test_secret_values_and_opaque_path_components_are_not_persistable(self):
        self.assertNotIn("ABC123", safe_text("api_key=ABC123 access_token=ABC123 client_secret=ABC123"))
        self.assertEqual("https://example.test/download/%5BREDACTED%5D",
                         safe_url("https://example.test/download/SECRET-TOKEN"))
        with self.assertRaises(CaptureError):
            stable_url("https://example.test/download/SECRET-TOKEN")

    def test_dns_rebinding_is_rejected_before_connection(self):
        class Handler(BaseHTTPRequestHandler):
            requests = 0

            def do_HEAD(self):
                type(self).requests += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://rebind.test:{server.server_port}/course"
            scope = Scope((url,))
            calls = {"count": 0}

            def rebind(*args, **kwargs):
                calls["count"] += 1
                address = "93.184.216.34" if calls["count"] % 2 else "127.0.0.1"
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, args[1]))]

            with patch("content_discovery_capture.runtimes.local.socket.getaddrinfo", rebind):
                with self.assertRaises(CaptureError):
                    HttpProvider().inspect(url, scope, 1024)
            self.assertGreaterEqual(calls["count"], 2)
            self.assertEqual(0, Handler.requests)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_ambiguous_numeric_ipv4_spellings_are_rejected(self):
        url = "http://0177.0.0.1:80/"
        with self.assertRaises(CaptureError):
            HttpProvider().open(url, Scope((url,)))

    def test_ambient_proxy_settings_do_not_redirect_http(self):
        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            with patch("urllib.request.getproxies", return_value={"http": "http://127.0.0.1:1"}), \
                    patch("urllib.request.proxy_bypass", return_value=False):
                response = HttpProvider().inspect(url, Scope((url,), allow_private_network=True), 1024)
            self.assertEqual("text/plain", response.headers.get("Content-Type"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_malformed_link_does_not_abort_other_navigation(self):
        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.reply(False)

            def do_GET(self):
                self.reply(True)

            def reply(self, body):
                if self.path.startswith("/course"):
                    data = b'<a href="http://127.0.0.1:99999/bad">bad</a><a href="/ok">ok</a>'
                    media = "text/html"
                else:
                    data, media = b"ok", "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if body:
                    self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as temp:
                app = Application(Path(temp) / "project")
                root = f"http://127.0.0.1:{server.server_port}/course"
                source = app.register_source("Course", "web", root,
                                             {"roots": [root], "allow_private_network": True})
                review = app.prepare_refresh([source["id"]])
                decide(app.store, review["id"], True)
                result = app.refresh(review["id"], steps=10)
                self.assertEqual("COMPLETE", result["sources"][0]["state"], result)
                self.assertIn(f"http://127.0.0.1:{server.server_port}/ok",
                              {o["location"] for o in app.store.all("snapshot")[0]["observations"]})
                app.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_derived_markdown_neutralizes_executable_schemes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.html"
            source.write_text('<a href="javascript:alert(1)">click</a>'
                              '<img src="data:text/html,<script>alert(2)</script>">')
            output = root / "output"
            derive(source, "text/html", "text", output, 30, 1024 * 1024, "https://example.test/course")
            text = (output / "reading.md").read_text()
            self.assertNotIn("javascript:", text.lower())
            self.assertNotIn("data:text", text.lower())

    def test_store_and_reports_are_private(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            app = Application(root)
            try:
                self.assertEqual(0o700, S_IMODE(root.stat().st_mode))
                self.assertEqual(0o700, S_IMODE(app.store.objects.stat().st_mode))
                self.assertEqual(0o700, S_IMODE(app.store.staging.stat().st_mode))
                self.assertEqual(0o600, S_IMODE((root / "project.sqlite3").stat().st_mode))
                create_manifest(app)
                reports = root / "reports"
                self.assertEqual(0o700, S_IMODE(reports.stat().st_mode))
                self.assertTrue(all(S_IMODE(path.stat().st_mode) == 0o600 for path in reports.iterdir()))
            finally:
                app.close()

    def test_hardlinked_project_file_is_not_ingested(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            source = root / "source"
            source.mkdir()
            app = Application(project)
            try:
                private = project / "private.txt"
                private.write_text("project secret")
                os.link(private, source / "alias.txt")
                registered = app.register_source("Source", "filesystem", str(source))
                review = app.prepare_refresh([registered["id"]])
                decide(app.store, review["id"], True)
                result = app.refresh(review["id"])
                self.assertEqual("COMPLETE", result["sources"][0]["state"])
                self.assertEqual([], app.store.all("artefact"))
            finally:
                app.close()

    def test_worker_manifest_cannot_escape_staging_directory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.txt"
            source.write_text("x")
            destination = root / "out"

            def forged_worker(command, **kwargs):
                (destination / "result.json").write_text('{"files":[{"name":"../escape","sha256":"00","media_type":"text/plain"}]}')
                return SimpleNamespace(returncode=0)

            with patch("content_discovery_capture.derivation.subprocess.run", side_effect=forged_worker):
                with self.assertRaises(CaptureError):
                    derive(source, "text/plain", "text", destination, 30, 1024)
            self.assertFalse((root / "escape").exists())

    def test_zip_slip_member_is_never_extracted(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.odt"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("content.xml", '<office:document xmlns:office="urn:office" xmlns:text="urn:text"><text:p>safe</text:p></office:document>')
                archive.writestr("../../escape.txt", "must not be extracted")
            output = root / "output"
            derive(source, "application/vnd.oasis.opendocument.text", "text", output, 30, 1024 * 1024)
            self.assertFalse((root.parent / "escape.txt").exists())


if __name__ == "__main__":
    import unittest
    unittest.main()
