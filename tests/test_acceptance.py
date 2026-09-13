"""Synthetic retrospective-shaped acceptance. No course material or credentials."""
import base64
import json
import tempfile
import unittest
from pathlib import Path

from content_discovery_capture.application import Application
from content_discovery_capture.domain import CaptureError
from content_discovery_capture.interfaces.review import decide
from content_discovery_capture.interfaces.tools import invoke
from content_discovery_capture.packaging import export_pack
from test_web import server


class AcceptanceTests(unittest.TestCase):
    def test_retrospective_shaped_inventory_and_selected_capture(self):
        # These numbers are fixture data, never production constants.
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGPUiGpiYGBgYgADAAwAAQilTe9WAAAAAElFTkSuQmCC")
        routes = {}
        links = []
        for i in range(49):
            links.append(f'<a href="/course/lesson/{i}" data-resource-id="lesson-{i}">Lesson {i}</a>')
            assets = "".join(f'<img alt="Image {n}" src="/course/image/{n}">' for n in range(i, 74, 49))
            attachments = f'<a href="/course/file/{i % 25}" data-resource-id="file-{i % 25}">Attachment {i % 25}</a>' if i < 27 else ""
            routes[f"/course/lesson/{i}"] = (200, "text/html", f'<html><title>Lesson {i}</title><h1>Lesson {i}</h1><p>Synthetic teaching material.</p>{assets}{attachments}</html>'.encode())
        routes["/course"] = (200, "text/html", ("<html><title>Collection</title>" + "".join(links) + "</html>").encode())
        for i in range(74):
            routes[f"/course/image/{i}"] = (200, "image/png", png) if i < 71 else (401, "text/plain", b"Unavailable")
        for i in range(25):
            routes[f"/course/file/{i}"] = (200, "application/octet-stream", f"Unique attachment {i}".encode())
        with tempfile.TemporaryDirectory() as temp, server(routes) as (url, counts):
            app = Application(temp)
            try:
                source = app.register_source("Synthetic course", "web", url + "/course", {"roots": [url + "/course"], "allow_private_network": True})
                review = app.prepare_refresh()
                decide(app.store, review["id"], True)
                result = app.refresh(review["id"], steps=1000)
                self.assertEqual("INCOMPLETE", result["sources"][0]["state"], result)
                self.assertIsNone(app.store.get("source", source["id"])["baseline"])
                snapshot = result["sources"][0]["snapshot_id"]
                rows = app.index([snapshot])["resources"]
                lessons = {r["resource_id"] for r in rows if "/lesson/" in r["location"]}
                images = {r["location"] for r in rows if "/image/" in r["location"]}
                attachments = [r for r in rows if "/file/" in r["location"] and r["parent"] and "/lesson/" in r["parent"]]
                self.assertEqual(49, len(lessons))
                self.assertEqual(74, len(images))
                self.assertEqual(27, len(attachments))
                self.assertEqual(25, len({r["resource_id"] for r in attachments}))
                self.assertEqual(3, len({r["location"] for r in rows if r["feasibility"]["status"] == "UNAVAILABLE"}))
                self.assertFalse(any(method == "GET" and "/file/" in path for method, path in counts))
                capture = app.prepare_capture([snapshot], [r["selection_id"] for r in attachments])
                decide(app.store, capture["id"], True)
                job = app.capture(capture["id"], steps=100)
                self.assertEqual("COMPLETE", job["state"])
                self.assertEqual(25, len(list(app.store.objects.iterdir())))
                self.assertEqual(27, len(app.store.all("provenance")))
                # Recover all available image placements, leaving the three explicit gaps.
                image_rows = [r for r in rows if "/image/" in r["location"] and r["feasibility"]["status"] != "UNAVAILABLE"]
                image_plan = app.prepare_capture([snapshot], [r["selection_id"] for r in image_rows])
                decide(app.store, image_plan["id"], True)
                self.assertEqual("COMPLETE", app.capture(image_plan["id"], steps=200)["state"])
                self.assertEqual(71, len([a for a in app.store.all("artefact") if a["media_type"] == "image/png"]))
                lesson_rows = [r for r in rows if "/lesson/" in r["location"]][:19]
                reading_plan = app.prepare_capture([snapshot], [r["selection_id"] for r in lesson_rows], derivatives=["text"])
                decide(app.store, reading_plan["id"], True)
                self.assertEqual("COMPLETE", app.capture(reading_plan["id"], steps=100)["state"])
                readings = [a for a in app.store.all("artefact") if a.get("derivation") == "text"]
                self.assertEqual(19, len(readings))
                for i in range(6):
                    combine = invoke(app, "prepare_compendium", {"artefact_ids": [a["id"] for a in readings[i::6]], "title": f"Unit {i + 1}"})
                    decide(app.store, combine["id"], True)
                    invoke(app, "build_compendium", {"review_id": combine["id"]})
                self.assertEqual(6, len([a for a in app.store.all("artefact") if a.get("derivation") == "compendium"]))
                pack = export_pack(app)
                self.assertTrue(Path(pack["path"]).exists())
            finally:
                app.close()

    def test_budget_renewal_preserves_discovery_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input"
            source.mkdir()
            for i in range(5):
                (source / f"{i}.txt").write_text(str(i))
            app = Application(root / "project")
            try:
                app.register_source("Files", "filesystem", str(source))
                review = app.prepare_refresh(budget={"max_actions": 2})
                decide(app.store, review["id"], True)
                first = app.refresh(review["id"])
                run_id = first["sources"][0]["run_id"]
                before = app.store.get("run", run_id)
                renewed = app.prepare_discovery_resume(run_id, {"max_actions": 100})
                decide(app.store, renewed["id"], True)
                after = app.refresh(renewed["id"])
                self.assertEqual(run_id, after["sources"][0]["run_id"])
                self.assertEqual("COMPLETE", after["sources"][0]["state"])
                self.assertEqual(6, app.store.get("run", run_id)["actions"])
                self.assertTrue(set(before["observations"]).issubset(app.store.get("run", run_id)["observations"]))
            finally:
                app.close()

    def test_compendium_is_multi_version_derivative(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input"
            source.mkdir()
            for i in range(2):
                (source / f"{i}.md").write_text(f"# Chapter {i}\n\nUnique content {i}.")
            app = Application(root / "project")
            try:
                app.register_source("Files", "filesystem", str(source))
                review = app.prepare_refresh()
                decide(app.store, review["id"], True)
                snapshot = app.refresh(review["id"])["sources"][0]["snapshot_id"]
                capture = app.prepare_capture([snapshot], [r["selection_id"] for r in app.index([snapshot])["resources"]])
                decide(app.store, capture["id"], True)
                app.capture(capture["id"])
                originals = app.store.all("artefact")
                combine = invoke(app, "prepare_compendium", {"artefact_ids": [a["id"] for a in originals], "title": "Collection reading"})
                decide(app.store, combine["id"], True)
                compendium = invoke(app, "build_compendium", {"review_id": combine["id"]})
                self.assertEqual(2, len(compendium["inputs"]))
                self.assertEqual({a["version_id"] for a in originals}, {i["version_id"] for i in compendium["inputs"]})
                self.assertEqual(3, len(app.store.all("artefact")))
            finally:
                app.close()

    def test_cheap_etag_check_reuses_navigation_but_checks_children(self):
        routes = {"/course": (200, "text/html", b'<html><a href="/course/file">File</a></html>'),
                  "/course/file": (200, "application/octet-stream", b"first")}
        with tempfile.TemporaryDirectory() as temp, server(routes) as (url, counts):
            app = Application(temp)
            try:
                app.register_source("Course", "web", url + "/course", {"roots": [url + "/course"], "allow_private_network": True})
                for _ in range(2):
                    review = app.prepare_refresh()
                    decide(app.store, review["id"], True)
                    app.refresh(review["id"])
                    routes["/course/file"] = (200, "application/octet-stream", b"changed")
                self.assertEqual(1, counts.count(("GET", "/course")))
                self.assertEqual(2, counts.count(("HEAD", "/course/file")))
                self.assertGreater(app.store.all("comparison")[-1]["counts"].get("POSSIBLY_CHANGED", 0), 0)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
