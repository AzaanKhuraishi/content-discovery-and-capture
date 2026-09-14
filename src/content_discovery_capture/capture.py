"""Checkpointed execution with finite attempts and per-plan budgets."""
from pathlib import Path
import time

from .domain import CaptureError, AccessRequired, RuntimeActionRequired, BudgetExceeded, Scope, digest, now, uid
from .verification import verify
from .derivation import derive, supported


def _record(app, job, item, result, attempt_id, method):
    store = app.store
    verification = verify(result.path, result.media_type, item["media_type"], result.role)
    obj = store.promote(result.path)
    version_id = "version_" + digest([item["resource_id"], obj["sha256"], result.role])[:32]
    artefact = {"id": uid("artefact"), "resource_id": item["resource_id"], "version_id": version_id,
        "observed_version_id": item["version_id"], "representation_id": item["representation_id"],
        "placement_id": item["placement_id"], "source_id": item["source_id"], "snapshot_id": item["snapshot_id"],
        "job_id": job["id"], "title": item["title"], "role": result.role, "media_type": result.media_type,
        "method": method, "method_version": "1", "captured_at": result.accessed_at, **obj,
        "limitations": result.limitations + verification["limitations"]}
    with store.transaction():
        if not store.get("version", version_id, optional=True):
            store.put("version", {"id": version_id, "resource_id": item["resource_id"],
                "sha256": obj["sha256"], "role": result.role, "first_observed": result.accessed_at,
                "observed_revision": item["version_id"], "evidence_level": "captured_content"})
        store.put("artefact", artefact)
        store.put("verification", {"id": uid("verification"), "artefact_id": artefact["id"],
            "sha256": obj["sha256"], "verified_at": now(), "verifier_version": "1", **verification})
        store.put("provenance", {"id": uid("provenance"), "artefact_id": artefact["id"],
            "source_id": item["source_id"], "snapshot_id": item["snapshot_id"], "version_id": version_id,
            "representation_id": item["representation_id"], "placement_id": item["placement_id"],
            "location": item["location"], "accessed_at": result.accessed_at, "attempt_id": attempt_id})
    return artefact


def execute(app, review, steps):
    store, payload = app.store, review["payload"]
    if hasattr(app.browser, "bind_context"):
        app.browser.bind_context(review["id"])
    recover(app)
    job_id = payload.get("resume_job_id") or "job_" + digest(review["id"])[:32]
    job = store.get("job", job_id, optional=True)
    if job and payload.get("resume_job_id") and job["review_id"] != review["id"]:
        job["review_id"], job["state"] = review["id"], "RUNNING"
        with store.transaction():
            store.put("job", job)
    if job is None:
        job = {"id": job_id, "review_id": review["id"], "state": "RUNNING", "created_at": now(),
            "bytes_spent": 0, "seconds_spent": 0, "items": {i["selection_id"]: {"state": "PENDING", "attempts": [],
            "artefact_id": None, "derivations": {d: {"state": "PENDING", "attempts": 0} for d in payload["derivatives"]}} for i in payload["items"]}}
        with store.transaction():
            store.put("job", job)
    if job["state"] in ("CANCELLED", "COMPLETE", "COMPLETE_WITH_GAPS", "BUDGET_REACHED"):
        return job
    job["state"] = "RUNNING"
    operations = 0
    for item in payload["items"]:
        task = job["items"][item["selection_id"]]
        if task["state"] in ("FAILED", "DONE"):
            continue
        adapter = app.adapters[store.get("source", item["source_id"])["kind"]]
        scope = Scope.from_dict(store.get("scope", item["scope_id"])["definition"])
        methods = [m for m in item["feasibility"]["methods"] if m in adapter.methods(item)]
        def awaiting_runtime():
            return bool(task["attempts"]) and store.get("attempt", task["attempts"][-1])["state"] == "WAITING_RUNTIME"
        while not task["artefact_id"] and (len(task["attempts"]) < payload["max_attempts"] or awaiting_runtime()) and operations < steps:
            left_bytes = payload["max_bytes"] - job["bytes_spent"]
            left_seconds = int(payload["max_seconds"] - job["seconds_spent"])
            if left_bytes <= 0 or left_seconds <= 0:
                job["state"] = "BUDGET_REACHED"
                break
            if not methods:
                task["state"], task["message"] = "FAILED", "No approved method is currently available."
                break
            previous_attempt = store.get("attempt", task["attempts"][-1]) if task["attempts"] else None
            method = previous_attempt["method"] if previous_attempt and previous_attempt["state"] in ("NEEDS_USER", "WAITING_RUNTIME") else methods[min(len(task["attempts"]), len(methods) - 1)]
            resuming_action = awaiting_runtime()
            attempt = previous_attempt if resuming_action else {"id": uid("attempt"), "job_id": job_id, "selection_id": item["selection_id"],
                "method": method, "method_version": "1", "started_at": now(), "state": "STARTED", "path": str(store.temp()),
                "budget_reserved_bytes": left_bytes, "budget_reserved_seconds": left_seconds}
            if not resuming_action:
                task["attempts"].append(attempt["id"])
            with store.transaction():
                store.put("attempt", attempt)
                store.put("job", job)
                store.event("capture_attempt_started", {"attempt_id": attempt["id"], "method": method})
            start = time.monotonic()
            operations += 1
            try:
                if hasattr(app.browser, "bind_context"):
                    app.browser.bind_context(attempt["id"])
                attempt_path = Path(attempt["path"])
                if (attempt_path.is_symlink() or attempt_path.parent.resolve() != store.staging.resolve()):
                    raise CaptureError("Capture staging path is outside the private project directory.")
                result = adapter.capture(item, scope, method, Path(attempt["path"]), left_bytes, left_seconds)
                bytes_used = result.path.stat().st_size
                # A prior crash may have committed an artefact but not the job checkpoint.
                artefact = _record(app, job, item, result, attempt["id"], method)
                task["artefact_id"], task["state"] = artefact["id"], "CAPTURED"
                attempt["state"], attempt["artefact_id"] = "VERIFIED", artefact["id"]
                job["bytes_spent"] += bytes_used
            except RuntimeActionRequired as error:
                attempt["state"], attempt["message"] = "WAITING_RUNTIME", str(error)
                job["state"] = "NEEDS_USER"
            except AccessRequired as error:
                attempt["state"], attempt["message"] = "NEEDS_USER", str(error)
                job["state"] = "NEEDS_USER"
            except (CaptureError, OSError) as error:
                attempt["state"] = "FAILED"
                attempt["message"] = str(error) if isinstance(error, CaptureError) else "Local capture operation failed."
                if isinstance(error, BudgetExceeded):
                    job["state"] = "BUDGET_REACHED"
                if "changed" in attempt["message"].lower():
                    task["state"], task["message"] = "FAILED", attempt["message"]
            except Exception:
                attempt["state"], attempt["message"] = "FAILED", "Runtime failed during capture; partial output retained."
            finally:
                if attempt["state"] != "VERIFIED" and Path(attempt["path"]).is_file():
                    job["bytes_spent"] += Path(attempt["path"]).stat().st_size
                job["seconds_spent"] += time.monotonic() - start
                attempt["finished_at"] = now()
                with store.transaction():
                    store.put("attempt", attempt)
                    store.put("job", job)
            if job["state"] in ("NEEDS_USER", "BUDGET_REACHED") or task["state"] == "FAILED":
                break
        if not task["artefact_id"]:
            if len(task["attempts"]) >= payload["max_attempts"] and not awaiting_runtime():
                task["state"], task["message"] = "FAILED", "Bounded attempts exhausted; inspect the attempt history."
            if operations >= steps or job["state"] in ("NEEDS_USER", "BUDGET_REACHED"):
                break
            continue
        original = store.get("artefact", task["artefact_id"])
        for operation, derivative in task["derivations"].items():
            if derivative["state"] in ("DONE", "UNAVAILABLE", "FAILED") or operations >= steps:
                continue
            if not supported(original["media_type"], operation):
                derivative["state"], derivative["message"] = "UNAVAILABLE", "Required representation, converter or local transcription model is unavailable."
                continue
            if derivative["attempts"] >= payload["max_attempts"]:
                derivative["state"] = "FAILED"
                continue
            left_bytes = payload["max_bytes"] - job["bytes_spent"]
            left_seconds = int(payload["max_seconds"] - job["seconds_spent"])
            if left_bytes <= 0 or left_seconds <= 0:
                job["state"] = "BUDGET_REACHED"
                break
            derivative["attempts"] += 1
            with store.transaction():
                store.put("job", job)
            operations += 1
            destination = store.staging / ("derived_" + digest([job_id, item["selection_id"], operation])[:32])
            start = time.monotonic()
            try:
                if destination.is_symlink() or destination.parent.resolve() != store.staging.resolve():
                    raise CaptureError("Derivation staging path is outside the private project directory.")
                if (destination / "result.json").exists():
                    import json
                    result = json.loads((destination / "result.json").read_text())
                else:
                    result = derive(store.object_path(original["sha256"]), original["media_type"], operation,
                                    destination, left_seconds, left_bytes, item["location"])
                if not isinstance(result.get("files"), list) or len(result["files"]) > 32:
                    raise CaptureError("Derivation returned an invalid output manifest.")
                outputs = []
                for output in result["files"]:
                    name = output.get("name") if isinstance(output, dict) else None
                    if (not isinstance(name, str) or not name or "\x00" in name or name in (".", "..")
                            or "/" in name or "\\" in name
                            or Path(name).name != name):
                        raise CaptureError("Derivation returned an invalid output filename.")
                    path = destination / name
                    if path.resolve(strict=False).parent != destination.resolve() or path.is_symlink():
                        raise CaptureError("Derived output left its staging directory.")
                    artefact_id = "artefact_" + digest([job_id, item["selection_id"], operation, name])[:32]
                    existing = store.get("artefact", artefact_id, optional=True)
                    if existing:
                        if store.hash_file(store.object_path(existing["sha256"])) != existing["sha256"]:
                            raise CaptureError("Previously committed derivative failed integrity verification.")
                        if artefact_id not in derivative.setdefault("accounted_artefacts", []):
                            job["bytes_spent"] += existing["size"]
                            derivative["accounted_artefacts"].append(artefact_id)
                        outputs.append(artefact_id)
                        continue
                    if not path.exists() and output.get("sha256"):
                        orphan = store.object_path(output["sha256"])
                        if orphan.exists() and store.hash_file(orphan) == output["sha256"]:
                            import shutil
                            shutil.copyfile(orphan, path)
                    if store.hash_file(path) != output["sha256"]:
                        raise CaptureError("Staged derivative does not match the worker output hash.")
                    verification = verify(path, output["media_type"], output["media_type"], "derived")
                    obj = store.promote(path)
                    artefact = {"id": artefact_id, "role": "derived", "derivation": operation,
                        "title": original["title"] + " — " + output["name"], "media_type": output["media_type"],
                        "inputs": [{"artefact_id": original["id"], "version_id": original["version_id"], "sha256": original["sha256"]}],
                        "version_id": original["version_id"], "resource_id": original["resource_id"], "source_id": original["source_id"],
                        "job_id": job_id, "method_version": result["worker_version"], "created_at": now(),
                        "limitations": result["limitations"], **obj}
                    with store.transaction():
                        store.put("artefact", artefact)
                        store.put("verification", {"id": uid("verification"), "artefact_id": artefact["id"], "sha256": obj["sha256"],
                                                   "verified_at": now(), "verifier_version": "1", **verification})
                        store.put("provenance", {"id": uid("provenance"), "artefact_id": artefact["id"], "inputs": artefact["inputs"],
                                                  "operation": operation, "created_at": now()})
                    job["bytes_spent"] += obj["size"]
                    derivative.setdefault("accounted_artefacts", []).append(artefact_id)
                    outputs.append(artefact["id"])
                derivative.update(state="DONE", artefact_ids=outputs)
            except CaptureError as error:
                derivative["message"] = str(error)
                derivative["state"] = "FAILED" if derivative["attempts"] >= payload["max_attempts"] else "PENDING"
                if destination.exists():
                    job["bytes_spent"] += sum(p.stat().st_size for p in destination.iterdir() if p.is_file() and p.name not in ("request.json", "result.json"))
            finally:
                job["seconds_spent"] += time.monotonic() - start
                with store.transaction():
                    store.put("job", job)
        if all(d["state"] in ("DONE", "UNAVAILABLE", "FAILED") for d in task["derivations"].values()):
            task["state"] = "DONE"
        if operations >= steps or job["state"] == "BUDGET_REACHED":
            break
    terminal = all(t["state"] in ("DONE", "FAILED") for t in job["items"].values())
    if terminal:
        gaps = any(t["state"] == "FAILED" or any(d["state"] != "DONE" for d in t["derivations"].values()) for t in job["items"].values())
        job["state"] = "COMPLETE_WITH_GAPS" if gaps else "COMPLETE"
    elif job["state"] not in ("NEEDS_USER", "BUDGET_REACHED"):
        job["state"] = "RUNNING"
    with store.transaction():
        store.put("job", job)
    if terminal and not job.get("manifest_id"):
        from .reporting import create_manifest
        manifest = create_manifest(app)
        job["manifest_id"] = manifest["id"]
        with store.transaction():
            store.put("job", job)
    return job


def recover(app):
    """Conservatively reconcile process interruption; never reset spent attempts."""
    recovered = []
    store = app.store
    for attempt in store.all("attempt"):
        if attempt["state"] != "STARTED":
            continue
        job = store.get("job", attempt["job_id"])
        task = job["items"][attempt["selection_id"]]
        provenance = next((p for p in store.all("provenance") if p.get("attempt_id") == attempt["id"]), None)
        if provenance:
            artefact = store.get("artefact", provenance["artefact_id"])
            if store.hash_file(store.object_path(artefact["sha256"])) != artefact["sha256"]:
                raise CaptureError("Recovered content failed integrity checking.")
            task["artefact_id"], task["state"] = artefact["id"], "CAPTURED"
            attempt["state"] = "VERIFIED"
            job["bytes_spent"] += artefact["size"]
        else:
            attempt["state"] = "INTERRUPTED"
            path = Path(attempt["path"])
            job["bytes_spent"] += path.stat().st_size if path.exists() else 0
            from datetime import datetime, timezone
            elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(attempt["started_at"])).total_seconds())
            job["seconds_spent"] += min(elapsed, attempt["budget_reserved_seconds"])
            job["state"] = "RUNNING"
        with store.transaction():
            store.put("attempt", attempt)
            store.put("job", job)
        recovered.append(attempt["id"])
    return recovered
