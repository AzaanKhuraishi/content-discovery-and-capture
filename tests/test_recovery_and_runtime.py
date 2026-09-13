from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from content_discovery_capture.application import Application
from content_discovery_capture.domain import CaptureError, AccessRequired
from content_discovery_capture.interfaces.review import decide
from content_discovery_capture.interfaces.tools import invoke
from content_discovery_capture.runtimes.host import pending, supply


class SimulatedCrash(BaseException):
    pass


class RecoveryTests(unittest.TestCase):
    def setup_project(self, root):
        source = root / "input"
        source.mkdir()
        (source / "a.txt").write_text("Original content")
        app = Application(root / "project")
        app.register_source("Files", "filesystem", str(source))
        refresh = app.prepare_refresh()
        decide(app.store, refresh["id"], True)
        snapshot = app.refresh(refresh["id"])["sources"][0]["snapshot_id"]
        plan = app.prepare_capture([snapshot], [r["selection_id"] for r in app.index([snapshot])["resources"]])
        decide(app.store, plan["id"], True)
        return app, plan

    def test_crash_after_artefact_commit_reconciles_without_recapture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app, plan = self.setup_project(root)
            original_put = app.store.put
            def crash(kind, record):
                if kind == "job" and app.store.all("artefact"):
                    raise SimulatedCrash()
                return original_put(kind, record)
            with patch.object(app.store, "put", crash):
                with self.assertRaises(SimulatedCrash):
                    app.capture(plan["id"])
            app.close()
            resumed = Application(root / "project")
            try:
                result = resumed.capture(plan["id"])
                self.assertEqual("COMPLETE", result["state"])
                self.assertEqual(1, len(resumed.store.all("artefact")))
                self.assertEqual(1, len(resumed.store.all("attempt")))
            finally:
                resumed.close()

    def test_partial_transfer_retains_attempt_and_resumes_only_pending_item(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app, plan = self.setup_project(root)
            def interrupted(obs, scope, method, destination, max_bytes, timeout):
                destination.write_bytes(b"part")
                raise SimulatedCrash()
            with patch.object(app.adapters["filesystem"], "capture", interrupted):
                with self.assertRaises(SimulatedCrash):
                    app.capture(plan["id"])
            app.close()
            resumed = Application(root / "project")
            try:
                job = resumed.capture(plan["id"])
                self.assertEqual("COMPLETE", job["state"])
                self.assertEqual(2, len(resumed.store.all("attempt")))
                self.assertEqual("INTERRUPTED", resumed.store.all("attempt")[0]["state"])
                self.assertEqual(1, len(resumed.store.all("artefact")))
            finally:
                resumed.close()

    def test_retry_budget_cannot_be_reset_by_restarting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app, plan = self.setup_project(root)
            def fail(*args):
                raise CaptureError("Permanent test failure")
            with patch.object(app.adapters["filesystem"], "capture", fail):
                result = app.capture(plan["id"])
            self.assertEqual("COMPLETE_WITH_GAPS", result["state"])
            app.close()
            resumed = Application(root / "project")
            try:
                resumed.capture(plan["id"])
                self.assertEqual(3, len(resumed.store.all("attempt")))
            finally:
                resumed.close()

    def test_host_browser_returns_real_observations_without_approval_authority(self):
        class AuthenticatedOnly:
            def inspect(self, *args):
                raise AccessRequired("Browser required")
            def download(self, *args):
                raise AccessRequired("Browser required")
        with tempfile.TemporaryDirectory() as temp:
            app = Application(temp, http=AuthenticatedOnly())
            try:
                app.configure_host_browser({"inspect": True, "snapshot": True, "download": False})
                app.register_source("Web", "web", "https://example.org/course")
                review = app.prepare_refresh()
                decide(app.store, review["id"], True)
                result = app.refresh(review["id"])
                self.assertEqual("NEEDS_USER", result["sources"][0]["state"])
                action = pending(app)[0]
                with self.assertRaises(CaptureError):
                    supply(app, action["id"], {"html": "<p>Wrong source</p>", "location": "https://elsewhere.example/", "complete": True})
                supply(app, action["id"], {"html": "<html><title>Course</title><h1>Observed content</h1></html>", "complete": True})
                result = app.resume_run(result["sources"][0]["run_id"])
                snapshot = result["sources"][0]["snapshot_id"]
                self.assertEqual("COMPLETE", result["sources"][0]["state"])
                capture = app.prepare_capture([snapshot], [r["selection_id"] for r in app.index([snapshot])["resources"]])
                decide(app.store, capture["id"], True)
                # HTTP access asks for user/session recovery first. After a recorded failed method,
                # a new approved method can be selected by the bounded capture runner.
                def failed_http(*args):
                    raise CaptureError("HTTP unavailable; use browser snapshot")
                app.adapters["web"].http.download = failed_http
                job = app.capture(capture["id"])
                self.assertEqual("NEEDS_USER", job["state"])
                action = pending(app)[0]
                self.assertEqual("browser-snapshot", action["request"]["operation"])
                Path(action["destination"]).write_text("<html><h1>Observed content</h1></html>")
                supply(app, action["id"], {"media_type": "text/html", "limitations": ["Rendered snapshot"]})
                job = app.capture(capture["id"])
                self.assertEqual("COMPLETE", job["state"])
                self.assertEqual(2, len(app.store.all("attempt")))
                self.assertEqual("snapshot", app.store.all("artefact")[0]["role"])
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
