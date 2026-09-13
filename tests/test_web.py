from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import tempfile
import threading
import unittest

from content_discovery_capture.application import Application
from content_discovery_capture.domain import Scope, CaptureError, ApprovalRequired, Budget
from content_discovery_capture.interfaces.review import decide, ReviewServer
from content_discovery_capture.sources.web import PageParser
from content_discovery_capture.verification import verify


@contextmanager
def server(routes):
    counts = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, body):
            counts.append((self.command, self.path))
            path = urlsplit(self.path).path
            response = routes.get(path, (404, "text/plain", b"Missing"))
            if callable(response):
                response = response(self)
            status, media, data = response
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", '"' + __import__("hashlib").sha256(data).hexdigest() + '"')
            self.end_headers()
            if body:
                self.wfile.write(data)
        def do_HEAD(self):
            self.reply(False)
        def do_GET(self):
            self.reply(True)
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", counts
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


class WebTests(unittest.TestCase):
    def test_no_binary_download_during_discovery_and_signed_urls_not_persisted(self):
        routes = {
            "/course": (200, "text/html", b'<html><title>Course</title><a href="/course/file?token=SECRET-EXAMPLE">Attachment</a><a href="https://outside.example/">External</a></html>'),
            "/course/file": (200, "application/octet-stream", b"original binary")}
        with tempfile.TemporaryDirectory() as temp, server(routes) as (url, counts):
            app = Application(temp)
            app.register_source("Course", "web", url + "/course", {"roots": [url + "/course"], "allow_private_network": True})
            review = app.prepare_refresh()
            self.assertEqual([], counts)
            decide(app.store, review["id"], True)
            result = app.refresh(review["id"])
            self.assertEqual("COMPLETE", result["sources"][0]["state"])
            self.assertFalse(any(method == "GET" and "/file" in path for method, path in counts))
            snapshot = result["sources"][0]["snapshot_id"]
            index = app.index([snapshot])
            self.assertEqual(1, sum(r["external"] for r in index["resources"]))
            selected = [r["selection_id"] for r in index["resources"] if r["title"] == "Attachment"]
            capture = app.prepare_capture([snapshot], selected)
            decide(app.store, capture["id"], True)
            self.assertEqual("COMPLETE", app.capture(capture["id"])["state"])
            persisted = "\n".join(row[0] for row in app.store.db.execute("SELECT data FROM records UNION ALL SELECT data FROM events"))
            self.assertNotIn("SECRET-EXAMPLE", persisted)
            self.assertTrue(any(method == "GET" and "/file" in path for method, path in counts))
            app.close()

    def test_external_candidate_cannot_be_captured(self):
        with tempfile.TemporaryDirectory() as temp, server({"/course": (200, "text/html", b'<a href="https://outside.example/file">External</a>')}) as (url, counts):
            app = Application(temp)
            app.register_source("Course", "web", url + "/course", {"roots": [url + "/course"], "allow_private_network": True})
            review = app.prepare_refresh()
            decide(app.store, review["id"], True)
            snapshot = app.refresh(review["id"])["sources"][0]["snapshot_id"]
            external = next(r for r in app.index([snapshot])["resources"] if r["external"])
            with self.assertRaises(CaptureError):
                app.prepare_capture([snapshot], [external["selection_id"]])
            app.close()

    def test_html_masquerading_as_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fake.pdf"
            path.write_text("<html>Please sign in</html>")
            with self.assertRaises(CaptureError):
                verify(path, "text/html", "application/pdf")

    def test_parser_discovers_frames_backgrounds_alternatives_and_media(self):
        parser = PageParser("https://example.org/course/")
        parser.feed('<iframe src="lesson"></iframe><div style="background:url(image.png)"></div>'
                    '<audio src="lecture"></audio><a rel="alternate" href="accessible">Text</a>')
        self.assertEqual({"frame", "background", "embeds", "alternative"}, {x["relation"] for x in parser.links})

    def test_scope_queries_and_encoded_traversal(self):
        scope = Scope(("https://example.org/course?course=123",))
        self.assertTrue(scope.permits("https://example.org/course/lesson?course=123&page=1", "web"))
        self.assertFalse(scope.permits("https://example.org/course/lesson?course=456", "web"))
        self.assertFalse(scope.permits("https://example.org/course/%2e%2e/admin?course=123", "web"))

    def test_local_review_rejects_forged_and_cross_origin_approval(self):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        import re
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp) / "project")
            app.register_source("Files", "filesystem", temp)
            review = app.prepare_refresh()
            panel = ReviewServer(app.store.root).start()
            url = panel.url + "/review/" + review["id"]
            try:
                page = urlopen(url).read().decode()
                nonce = re.search(r'name="nonce" value="([^"]+)"', page)[1]
                source = review["payload"]["sources"][0]["source_id"]
                data = urlencode({"nonce": nonce, "decision": "approve", "selected": source}).encode()
                with self.assertRaises(HTTPError):
                    urlopen(Request(url, data=data, headers={"Origin": "https://outside.example"}))
                self.assertEqual("PENDING", app.store.get("review", review["id"])["state"])
                urlopen(Request(url, data=data, headers={"Origin": panel.url})).read()
                self.assertEqual("APPROVED", app.store.get("review", review["id"])["state"])
                with self.assertRaises(HTTPError):
                    urlopen(Request(url, data=data, headers={"Origin": panel.url}))
            finally:
                panel.close()
                app.close()

    @unittest.skipUnless(os.environ.get("CDC_TEST_BROWSER"), "Set CDC_TEST_BROWSER=1 with the browser extra installed")
    def test_real_browser_nested_lazy_discovery(self):
        from content_discovery_capture.runtimes.playwright import PlaywrightProvider
        page = b'''<html><title>Course</title><details><summary>Week 1</summary>
          <details><summary>Nested folder</summary><a href="/course/lesson">Lesson</a></details></details>
          <button onclick="this.insertAdjacentHTML('beforebegin','<a href=/course/late>Late lesson</a>');this.remove()">Load more</button>
          <script>const x=1;</script></html>'''
        routes = {"/course": (200, "text/html", page), "/course/lesson": (200, "text/html", b"<h1>Lesson</h1>"),
                  "/course/late": (200, "text/html", b"<h1>Late lesson</h1>")}
        with tempfile.TemporaryDirectory() as temp, server(routes) as (url, counts):
            browser = PlaywrightProvider(headless=True)
            app = Application(temp, browser=browser)
            try:
                app.register_source("Course", "web", url + "/course", {"roots": [url + "/course"], "allow_private_network": True})
                review = app.prepare_refresh()
                decide(app.store, review["id"], True)
                result = app.refresh(review["id"])
                self.assertEqual("COMPLETE", result["sources"][0]["state"], result)
                locations = {r["location"] for r in app.index()["resources"]}
                self.assertIn(url + "/course/late", locations)
                self.assertIn(url + "/course/lesson", locations)
                self.assertEqual([], app.store.all("artefact"))
            finally:
                app.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
