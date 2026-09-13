import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from content_discovery_capture.application import Application
from content_discovery_capture.domain import Scope, CaptureError, ApprovalRequired, Observation, DiscoveryBatch, AccessRequired
from content_discovery_capture.interfaces.review import decide
from content_discovery_capture.interfaces.tools import invoke, definitions
from content_discovery_capture.packaging import export_pack
from content_discovery_capture.planning import compare
from content_discovery_capture.capture import recover


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.source.mkdir()
        self.app = Application(self.root / "project")

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def register(self, name="Files", directory=None):
        return self.app.register_source(name, "filesystem", str(directory or self.source))

    def scan(self, ids=None, budget=None):
        review = self.app.prepare_refresh(ids, budget)
        decide(self.app.store, review["id"], True)
        result = self.app.refresh(review["id"])
        while any(s["state"] == "RUNNING" for s in result["sources"]):
            result = self.app.refresh(review["id"])
        return result

    def capture_all(self, snapshots, derivatives=None):
        index = self.app.index(snapshots)
        selected = [i["selection_id"] for i in index["resources"] if not i["external"]]
        review = self.app.prepare_capture(snapshots, selected, derivatives=derivatives)
        decide(self.app.store, review["id"], True)
        result = self.app.capture(review["id"])
        for _ in range(100):
            if result["state"] != "RUNNING":
                return result
            result = self.app.capture(review["id"])
        self.fail("Capture did not terminate")

    def test_source_selection_precedes_io_and_capture_gate(self):
        (self.source / "sample.txt").write_text("One resource")
        source = self.register()
        review = self.app.prepare_refresh([source["id"]])
        self.assertEqual([], self.app.store.all("run"))
        with self.assertRaises(ApprovalRequired):
            self.app.refresh(review["id"])
        decide(self.app.store, review["id"], True)
        snapshot = self.app.refresh(review["id"])["sources"][0]["snapshot_id"]
        index = self.app.index([snapshot])
        capture = self.app.prepare_capture([snapshot], [index["resources"][0]["selection_id"]])
        with self.assertRaises(ApprovalRequired):
            self.app.capture(capture["id"])
        self.assertEqual([], list(self.app.store.objects.iterdir()))

    def test_index_only_completes_without_original_changes(self):
        file = self.source / "data.txt"
        file.write_text("Content")
        before = file.stat()
        self.register()
        result = self.scan()
        self.assertEqual("COMPLETE", result["sources"][0]["state"])
        self.assertEqual([], self.app.store.all("artefact"))
        self.assertEqual(before.st_mtime_ns, file.stat().st_mtime_ns)
        self.assertEqual("Content", file.read_text())

    def test_capture_and_derive_keep_specific_versions(self):
        file = self.source / "note.txt"
        file.write_text("Version one")
        self.register()
        first = self.scan()["sources"][0]["snapshot_id"]
        self.assertEqual("COMPLETE", self.capture_all([first], ["text"])["state"])
        first_artefacts = self.app.store.all("artefact")
        file.write_text("Version two adds a section")
        second = self.scan()["sources"][0]["snapshot_id"]
        self.assertEqual("COMPLETE", self.capture_all([second], ["text"])["state"])
        artefacts = self.app.store.all("artefact")
        self.assertEqual(4, len(artefacts))
        self.assertTrue(all(a in artefacts for a in first_artefacts))
        originals = [a for a in artefacts if a["role"] == "original"]
        self.assertNotEqual(originals[0]["version_id"], originals[1]["version_id"])
        for derived in [a for a in artefacts if a["role"] == "derived"]:
            parent = self.app.store.get("artefact", derived["inputs"][0]["artefact_id"])
            self.assertEqual(parent["version_id"], derived["version_id"])
            self.assertEqual(parent["sha256"], derived["inputs"][0]["sha256"])
        self.assertNotIn("superseded", json.dumps(artefacts))

    def test_cross_source_dedup_retains_provenance(self):
        other = self.root / "another"
        other.mkdir()
        (self.source / "same.txt").write_text("Identical bytes")
        (other / "same.txt").write_text("Identical bytes")
        self.register()
        self.register("Another source", other)
        snapshots = [s["snapshot_id"] for s in self.scan()["sources"]]
        result = self.capture_all(snapshots)
        self.assertEqual("COMPLETE", result["state"])
        self.assertEqual(1, len(list(self.app.store.objects.iterdir())))
        artefacts = self.app.store.all("artefact")
        self.assertEqual(2, len(artefacts))
        self.assertNotEqual(artefacts[0]["resource_id"], artefacts[1]["resource_id"])
        self.assertEqual(2, len(self.app.store.all("provenance")))

    def test_name_collision_is_not_duplicate(self):
        other = self.root / "another"
        other.mkdir()
        (self.source / "same.txt").write_text("First")
        (other / "same.txt").write_text("Second")
        self.register()
        self.register("Another", other)
        snapshots = [s["snapshot_id"] for s in self.scan()["sources"]]
        self.capture_all(snapshots)
        self.assertEqual(2, len(list(self.app.store.objects.iterdir())))
        from content_discovery_capture.packaging import organisation
        self.assertEqual(1, len(organisation(self.app.store.all("artefact"))["name_conflicts"]))

    def test_changed_after_review_is_not_silently_captured(self):
        file = self.source / "sample.txt"
        file.write_text("Before")
        self.register()
        snapshot = self.scan()["sources"][0]["snapshot_id"]
        file.write_text("After and different")
        result = self.capture_all([snapshot])
        self.assertEqual("COMPLETE_WITH_GAPS", result["state"])
        self.assertEqual([], self.app.store.all("artefact"))
        self.assertEqual(1, len(self.app.store.all("attempt")))

    def test_rename_is_location_change_not_new_resource(self):
        (self.source / "before.txt").write_text("Identical")
        self.register()
        self.scan()
        (self.source / "before.txt").rename(self.source / "after.txt")
        self.scan()
        comparison = self.app.store.all("comparison")[-1]
        self.assertEqual(1, comparison["counts"]["MOVED/RENAMED"])
        self.assertNotIn("NEW", comparison["counts"])

    def test_auth_failure_keeps_baseline(self):
        (self.source / "data.txt").write_text("Data")
        source = self.register()
        baseline = self.scan()["sources"][0]["snapshot_id"]
        adapter = self.app.adapters["filesystem"]
        original = adapter.discover
        def denied(*args):
            raise AccessRequired("Authenticate")
        adapter.discover = denied
        result = self.scan()
        self.assertEqual("NEEDS_USER", result["sources"][0]["state"])
        self.assertEqual(baseline, self.app.store.get("source", source["id"])["baseline"])
        adapter.discover = original
        resumed = self.app.resume_run(result["sources"][0]["run_id"])
        self.assertEqual("COMPLETE", resumed["sources"][0]["state"])
        self.assertNotIn("MISSING", self.app.store.all("comparison")[-1]["counts"])

    def test_incomplete_does_not_replace_baseline_or_claim_missing(self):
        for i in range(5):
            (self.source / f"{i}.txt").write_text(str(i))
        source = self.register()
        baseline = self.scan()["sources"][0]["snapshot_id"]
        result = self.scan(budget={"max_actions": 1})
        run = self.app.close_run(result["sources"][0]["run_id"])
        self.assertEqual("INCOMPLETE", run["state"])
        self.assertEqual(baseline, self.app.store.get("source", source["id"])["baseline"])
        self.assertNotIn("MISSING", self.app.store.all("comparison")[-1]["counts"])

    def test_snapshot_immutable(self):
        (self.source / "data.txt").write_text("x")
        self.register()
        snapshot = self.app.store.get("snapshot", self.scan()["sources"][0]["snapshot_id"])
        snapshot["complete"] = False
        with self.assertRaises(CaptureError):
            self.app.store.put("snapshot", snapshot)

    def test_scope_change_invalidates_approval(self):
        source = self.register()
        review = self.app.prepare_refresh()
        decide(self.app.store, review["id"], True)
        self.app.update_source(source["id"], scope={"roots": [str(self.source)], "excludes": ["*.secret"]})
        with self.assertRaises(ApprovalRequired):
            self.app.refresh(review["id"])

    def test_disable_source_retains_capture(self):
        (self.source / "data.txt").write_text("x")
        source = self.register()
        self.capture_all([self.scan()["sources"][0]["snapshot_id"]])
        self.app.update_source(source["id"], state="removed")
        self.assertEqual(1, len(self.app.store.all("artefact")))
        with self.assertRaises(CaptureError):
            self.app.prepare_refresh([source["id"]])

    def test_symlinks_and_output_recursion(self):
        (self.source / "outside").symlink_to(self.root / "project", target_is_directory=True)
        (self.source / "loop").symlink_to(self.source, target_is_directory=True)
        (self.source / "ok.txt").write_text("safe")
        self.register()
        result = self.scan()
        self.assertEqual(1, len(self.app.index()["resources"]))
        self.assertEqual("COMPLETE", result["sources"][0]["state"])

    def test_pack_contains_only_selected_allowlisted_payloads(self):
        (self.source / "a.txt").write_text("A")
        self.register()
        self.capture_all([self.scan()["sources"][0]["snapshot_id"]], ["text"])
        result = export_pack(self.app, include_originals=False)
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
            self.assertNotIn("project.sqlite3", names)
            self.assertFalse(any(n.startswith("originals/") for n in names))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(1, len(manifest["exclusions"]))
            self.assertNotIn(str(self.root), json.dumps(manifest))
            import hashlib
            for line in archive.read("MANIFEST.sha256").decode().splitlines():
                h, name = line.split("  ", 1)
                self.assertEqual(h, hashlib.sha256(archive.read(name)).hexdigest())

    def test_no_model_tool_can_approve(self):
        self.assertFalse(any(d["name"] in ("approve", "decide", "approve_review") for d in definitions()))
        with self.assertRaises(CaptureError):
            invoke(self.app, "approve", {"approved": True})

    def test_resume_prompt_does_not_scan(self):
        self.register()
        self.app.set_resume_prompt(True)
        self.assertTrue(self.app.resume_status()["offer_refresh"])
        self.assertEqual([], self.app.store.all("run"))

    def test_opaque_empty_original_is_preserved(self):
        (self.source / "empty.bin").touch()
        self.register()
        result = self.capture_all([self.scan()["sources"][0]["snapshot_id"]])
        self.assertEqual("COMPLETE", result["state"])
        self.assertEqual(0, self.app.store.all("artefact")[0]["size"])

    def test_restarting_finished_job_is_idempotent(self):
        (self.source / "a.txt").write_text("a")
        self.register()
        result = self.capture_all([self.scan()["sources"][0]["snapshot_id"]])
        before = len(self.app.store.all("attempt"))
        self.app.close()
        self.app = Application(self.root / "project")
        self.app.capture(result["review_id"])
        self.assertEqual(before, len(self.app.store.all("attempt")))


if __name__ == "__main__":
    unittest.main()
