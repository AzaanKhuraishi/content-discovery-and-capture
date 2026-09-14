"""Deterministic application service. All source I/O is behind reviewed decisions."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import time

from .domain import (Scope, Budget, CaptureError, ApprovalRequired, AccessRequired, RuntimeActionRequired, BudgetExceeded,
                     canonical, digest, now, uid, safe_data, stable_url)
from .persistence import Store
from .planning import version_evidence, compare, estimate, feasibility
from .runtimes.local import capabilities
from .sources.filesystem import FilesystemAdapter
from .sources.web import WebAdapter


class Application:
    def __init__(self, root, *, browser=None, http=None):
        self.store = Store(root)
        self.browser = browser
        self.adapters = {"web": WebAdapter(http, browser), "filesystem": FilesystemAdapter(self.store.root)}
        if not self.store.get("project", "project", optional=True):
            with self.store.transaction():
                self.store.put("project", {"id": "project", "created_at": now(), "prompt_on_resume": False})

    def close(self):
        self.store.close()

    def configure_host_browser(self, offered):
        from .runtimes.host import HostBrowserProvider
        if set(offered) - {"inspect", "snapshot", "download"} or any(type(v) is not bool for v in offered.values()):
            raise CaptureError("Advertise only inspect, snapshot and download capabilities actually available to the host.")
        self.browser = HostBrowserProvider(self.store, offered)
        self.adapters["web"].browser = self.browser
        return self.browser.capabilities()

    def registry(self):
        sources = []
        snapshots = self.store.all("snapshot")
        for source in self.store.all("source"):
            sources.append({**source, "scope": self.store.get("scope", source["scope_id"])["definition"],
                "discovery_history": [{"snapshot_id": s["id"], "complete": s["complete"], "completed_at": s["completed_at"]}
                                      for s in snapshots if s["source_id"] == source["id"]]})
        return {"sources": sources, "message": "Choose sources to check, add a new location, disable/remove a source, or cancel. No source has been scanned."}

    def register_source(self, name, kind, location, scope=None, access_requirement="none"):
        if kind not in self.adapters:
            raise CaptureError("V1 supports only Web and Filesystem sources.")
        location = stable_url(location) if kind == "web" else str(Path(location).expanduser().absolute())
        scope = Scope.from_dict(scope) if scope else Scope((location,))
        if kind == "web":
            for root in scope.roots:
                stable_url(root)
            for origin in scope.asset_origins:
                stable_url(origin)
        else:
            scope = Scope(tuple(str(Path(r).expanduser().absolute()) for r in scope.roots), scope.excludes,
                          (), scope.max_depth, False)
        if not scope.permits(location, kind):
            raise CaptureError("The source location must be within its explicit scope.")
        for source in self.store.all("source"):
            if source["kind"] == kind and source["location"] == location and source["state"] != "removed":
                raise CaptureError("This location is already registered. Update its scope or re-enable it.")
        source_id = uid("source")
        scoped = {"id": uid("scope"), "source_id": source_id, "definition": scope.data(), "created_at": now()}
        source = {"id": source_id, "name": safe_data(name), "kind": kind, "location": location,
                  "scope_id": scoped["id"], "state": "enabled", "availability": "UNKNOWN", "availability_at": None,
                  "access_requirement": safe_data(access_requirement), "last_attempt": None,
                  "last_success": None, "baseline": None, "created_at": now()}
        with self.store.transaction():
            self.store.put("scope", scoped)
            self.store.put("source", source)
            self.store.event("source_registered", source)
        return source

    def update_source(self, source_id, *, state=None, scope=None, name=None):
        source = self.store.get("source", source_id)
        if state is not None:
            if state not in ("enabled", "disabled", "removed"):
                raise CaptureError("Choose enabled, disabled or removed.")
            source["state"] = state
        if name is not None:
            source["name"] = safe_data(name)
        with self.store.transaction():
            if scope is not None:
                definition = Scope.from_dict(scope)
                if not definition.permits(source["location"], source["kind"]):
                    raise CaptureError("Source must remain inside its scope.")
                if source["kind"] == "web":
                    for root in definition.roots:
                        stable_url(root)
                    for origin in definition.asset_origins:
                        stable_url(origin)
                revision = {"id": uid("scope"), "source_id": source_id, "definition": definition.data(), "created_at": now()}
                self.store.put("scope", revision)
                source["scope_id"] = revision["id"]
            self.store.put("source", source)
            self.store.event("source_updated", {"source_id": source_id, "scope_id": source["scope_id"], "state": source["state"]})
        return source

    def set_resume_prompt(self, enabled):
        project = self.store.get("project", "project")
        project["prompt_on_resume"] = bool(enabled)
        with self.store.transaction():
            self.store.put("project", project)
        return project

    def classify(self, resource_id, labels, basis, evidence):
        self.store.get("resource", resource_id)
        if basis not in ("user", "placement", "metadata", "captured-content"):
            raise CaptureError("Classification needs a factual basis: user, placement, metadata or captured-content.")
        if not labels or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise CaptureError("Provide non-empty classification labels.")
        if any(label.casefold() in ("stale", "obsolete", "irrelevant", "superseded") for label in labels):
            raise CaptureError("Relevance and supersession judgments belong to consuming applications.")
        record = {"id": uid("classification"), "resource_id": resource_id, "labels": safe_data(labels),
                  "basis": basis, "evidence": safe_data(evidence), "created_at": now()}
        with self.store.transaction():
            self.store.put("classification", record)
        return record

    def resume_status(self):
        return {"offer_refresh": self.store.get("project", "project")["prompt_on_resume"],
                "runs": self.store.all("run"), "jobs": self.store.all("job"),
                "message": "Checking for changes is optional and requires source selection. Saved work can resume independently."}

    def _review(self, kind, payload):
        review = {"id": uid("review"), "kind": kind, "payload": payload, "payload_hash": digest(payload),
                  "state": "PENDING", "created_at": now()}
        with self.store.transaction():
            self.store.put("review", review)
        return review

    def prepare_refresh(self, source_ids=None, budget=None):
        # Registry reads only. No capability probes or source I/O before this decision.
        active = [s for s in self.store.all("source") if s["state"] == "enabled"]
        selected = set(source_ids) if source_ids is not None else {s["id"] for s in active}
        if not selected or selected - {s["id"] for s in active}:
            raise CaptureError("Select at least one enabled registered source.")
        limits = Budget(**(budget or {}))
        sources = [{"source_id": s["id"], "name": s["name"], "location": s["location"],
                    "scope_id": s["scope_id"], "scope": self.store.get("scope", s["scope_id"])["definition"],
                    "last_success": s["last_success"]} for s in active if s["id"] in selected]
        warnings = []
        for item in sources:
            if item["scope"].get("allow_private_network"):
                warnings.append(f"{item['name']}: local/private network access is included in this scope.")
            if item["scope"].get("roots"):
                for root in item["scope"]["roots"]:
                    if not root.startswith("/"):
                        continue
                    try:
                        resolved = Path(root).resolve()
                        if resolved == Path("/") or resolved == Path.home() or Path.home().is_relative_to(resolved):
                            warnings.append(f"{item['name']}: this filesystem scope is broad and may include sensitive personal files.")
                            break
                    except (OSError, RuntimeError):
                        continue
        return self._review("discovery", {"sources": sources, "budget_per_source": asdict(limits),
            "estimate": "Collection size is unknown until inspected. Discovery may read up to the displayed byte/action/time limits per source.",
            "warnings": warnings,
            "choices": "Check selected sources, return to add a new location, or cancel. Capture is a separate decision."})

    def prepare_discovery_resume(self, run_id, budget):
        run = self.store.get("run", run_id)
        if run.get("snapshot_id"):
            raise CaptureError("This scan is closed. Start a new refresh to check for changes.")
        review = self.prepare_refresh([run["source_id"]], budget)
        review["payload"]["resume_run_id"] = run_id
        review["payload"]["spent_usage"] = {k: run[k] for k in ("actions", "bytes_read", "seconds")}
        review["payload_hash"] = digest(review["payload"])
        with self.store.transaction():
            self.store.put("review", review)
        return review

    def prepare_capture_resume(self, job_id, max_bytes, max_seconds, max_attempts):
        job = self.store.get("job", job_id)
        previous = self.store.get("review", job["review_id"])
        if job["state"] in ("COMPLETE", "COMPLETE_WITH_GAPS"):
            raise CaptureError("This capture job is complete. Review a new plan for any additional work.")
        if not (max_bytes > job["bytes_spent"] and max_seconds > job["seconds_spent"] and 1 <= max_attempts <= 10):
            raise CaptureError("Renewed totals must exceed spent usage, with 1–10 attempts per item.")
        return self._review("capture", {**previous["payload"], "resume_job_id": job_id,
            "max_bytes": max_bytes, "max_seconds": max_seconds, "max_attempts": max_attempts,
            "spent_usage": {k: job[k] for k in ("bytes_spent", "seconds_spent")}})

    def _approved(self, review_id, kind):
        review = self.store.get("review", review_id)
        if review["state"] != "APPROVED" or review["kind"] != kind or digest(review["payload"]) != review["payload_hash"]:
            raise ApprovalRequired("A user must approve this exact plan in the local review panel.")
        for spec in review["payload"].get("sources", []):
            source = self.store.get("source", spec["source_id"])
            if source["state"] != "enabled" or source["scope_id"] != spec["scope_id"]:
                raise ApprovalRequired("Source selection or scope changed; review the revised plan.")
        return review

    def refresh(self, review_id, steps=100):
        from .persistence import project_lock
        with project_lock(self.store.root):
            return self._refresh(review_id, steps)

    def _refresh(self, review_id, steps=100):
        review = self._approved(review_id, "discovery")
        limits = Budget(**review["payload"]["budget_per_source"])
        runs = []
        for selection in review["payload"]["sources"]:
            source = self.store.get("source", selection["source_id"])
            run_id = review["payload"].get("resume_run_id") or "run_" + digest([review_id, source["id"]])[:32]
            run = self.store.get("run", run_id, optional=True)
            if run and review["payload"].get("resume_run_id") and run["review_id"] != review_id:
                if run["source_id"] != source["id"] or run["scope_id"] != source["scope_id"]:
                    raise ApprovalRequired("Resume must retain the scan's original source and scope.")
                run["review_id"], run["state"] = review_id, "PAUSED"
                with self.store.transaction():
                    self.store.put("run", run)
            if run is None:
                scope = self.store.get("scope", source["scope_id"])["definition"]
                run = {"id": run_id, "source_id": source["id"], "scope_id": source["scope_id"],
                       "review_id": review_id, "state": "RUNNING", "started_at": now(), "baseline": source["baseline"],
                       "frontier": [{"location": root, "depth": 0} for root in scope["roots"]], "visited": [],
                       "observations": {}, "gaps": [], "bytes_read": 0, "actions": 0, "seconds": 0,
                       "capabilities": capabilities(self.browser)}
                with self.store.transaction():
                    self.store.put("run", run)
            if run["state"] in ("RUNNING", "PAUSED"):
                run = self._discover(run, limits, min(max(1, steps), 1000))
            runs.append(run)
        return {"review_id": review_id, "sources": [{"source_id": r["source_id"], "run_id": r["id"], "state": r["state"],
                 "resources": len(r["observations"]), "snapshot_id": r.get("snapshot_id"), "message": r.get("message")} for r in runs]}

    def _discover(self, run, budget, steps):
        source = self.store.get("source", run["source_id"])
        scope = Scope.from_dict(self.store.get("scope", run["scope_id"])["definition"])
        adapter = self.adapters[source["kind"]]
        if hasattr(self.browser, "bind_context"):
            self.browser.bind_context(run["id"])
        if source["kind"] == "web" and run["baseline"]:
            baseline = self.store.get("snapshot", run["baseline"])
            adapter.http.previous = {o["location"]: o for o in baseline["observations"]
                                     if not o.get("metadata", {}).get("reference_only")}
        run["state"] = "RUNNING"
        source["last_attempt"] = now()
        for _ in range(steps):
            if not run["frontier"]:
                return self._finish_scan(run, not run["gaps"])
            if (run["actions"] >= budget.max_actions or run["bytes_read"] >= budget.max_bytes
                    or run["seconds"] >= budget.max_seconds or len(run["observations"]) >= budget.max_items):
                run["state"], run["message"] = "PAUSED", "Discovery budget reached. Close as incomplete or review a larger discovery budget."
                break
            cursor = run["frontier"][0]
            key = canonical([cursor["location"], cursor.get("offset", 0), cursor.get("after", ""), cursor.get("stamp")])
            if key in run["visited"]:
                run["frontier"].pop(0)
                continue
            start = time.monotonic()
            run["actions"] += 1  # Reserve an operation before calling a fallible provider.
            with self.store.transaction():
                self.store.put("run", run)
                self.store.event("inspection_started", {"run_id": run["id"], "location": cursor["location"]})
            try:
                remaining = Budget(max_items=max(1, budget.max_items - len(run["observations"])),
                    max_bytes=max(1, budget.max_bytes - run["bytes_read"]), max_seconds=max(1, int(budget.max_seconds - run["seconds"])),
                    max_actions=max(1, budget.max_actions - run["actions"]),
                    sample_bytes=min(budget.sample_bytes, max(1, budget.max_bytes - run["bytes_read"])))
                batch = adapter.discover(source, scope, cursor, remaining)
                run["bytes_read"] += batch.bytes_read
                run["actions"] += max(0, batch.actions - 1)
                run["frontier"] = batch.frontier + run["frontier"][1:]
                run["visited"].append(key)
                run["gaps"].extend(g for g in batch.gaps if not g.get("excluded"))
                for obs in batch.observations:
                    data = obs.data()
                    resource_id = "resource_" + digest([source["id"], data["identity"]])[:32]
                    data["resource_id"] = resource_id
                    obs_key = digest([resource_id, data["location"], data["parent"]])
                    existing = run["observations"].get(obs_key)
                    if not existing or not data["metadata"].get("reference_only"):
                        run["observations"][obs_key] = data
                source["availability"], source["availability_at"] = "ACCESSIBLE", now()
            except AccessRequired as error:
                if not isinstance(error, RuntimeActionRequired) and source["kind"] == "web" and cursor.get("relation") in ("embeds", "background"):
                    run["gaps"].append({"location": cursor["location"], "reason": str(error), "known_inaccessible_resource": True})
                    for observed in run["observations"].values():
                        if observed["location"] == cursor["location"]:
                            observed["metadata"]["capture_unavailable"] = True
                    run["frontier"].pop(0)
                else:
                    run["state"], run["message"] = "NEEDS_USER", str(error)
                    source["availability"], source["availability_at"] = "ACCESS_REQUIRED", now()
            except BudgetExceeded as error:
                run["state"], run["message"] = "PAUSED", str(error)
            except CaptureError as error:
                run["gaps"].append({"location": cursor["location"], "reason": str(error)})
                run["frontier"].pop(0)
                source["availability"], source["availability_at"] = "PARTIAL", now()
            except Exception:
                run["state"], run["message"] = "PAUSED", "Runtime failed. Check the runtime and resume; completed checkpoints are retained."
            finally:
                run["seconds"] += time.monotonic() - start
                with self.store.transaction():
                    self.store.put("source", source)
                    self.store.put("run", run)
                    self.store.event("inspection_checkpoint", {"run_id": run["id"], "state": run["state"]})
            if run["state"] != "RUNNING":
                break
        if not run["frontier"]:
            return self._finish_scan(run, not run["gaps"])
        return run

    def resume_run(self, run_id, steps=100):
        run = self.store.get("run", run_id)
        self._approved(run["review_id"], "discovery")
        if run["state"] == "NEEDS_USER":
            with self.store.transaction():
                run["state"] = "PAUSED"
                self.store.put("run", run)
        return self.refresh(run["review_id"], steps)

    def close_run(self, run_id):
        run = self.store.get("run", run_id)
        if run.get("snapshot_id"):
            return run
        run["gaps"].append({"reason": "Discovery closed before complete coverage"})
        return self._finish_scan(run, False)

    def _finish_scan(self, run, complete):
        source = self.store.get("source", run["source_id"])
        observations = list(run["observations"].values())
        # Upgrade reference placements using the inspected representation, preserving each placement.
        inspected = {o["resource_id"]: o for o in observations if not o["metadata"].get("reference_only")}
        snapshot_id = uid("snapshot")
        with self.store.transaction():
            for index, obs in enumerate(observations):
                if obs["metadata"].get("reference_only") and obs["resource_id"] in inspected:
                    obs = {**inspected[obs["resource_id"]], "parent": obs["parent"], "relation": obs["relation"]}
                    observations[index] = obs
                resource = self.store.get("resource", obs["resource_id"], optional=True)
                if resource is None:
                    resource = {"id": obs["resource_id"], "first_observed": obs["observed_at"], "title": obs["title"],
                                "identity_evidence": obs["metadata"].get("identity_basis", "stable-location")}
                resource["last_observed"] = obs["observed_at"]
                self.store.put("resource", resource)
                version_id = "version_" + digest([obs["resource_id"], version_evidence(obs)])[:32]
                if not self.store.get("version", version_id, optional=True):
                    self.store.put("version", {"id": version_id, "resource_id": obs["resource_id"],
                        "evidence": version_evidence(obs), "first_observed": obs["observed_at"],
                        "evidence_level": "content_hash" if obs.get("content_hash") else "observed_metadata",
                        "note": "An observed revision; content identity is established separately at capture."})
                rep_id = "representation_" + digest([version_id, obs["location"], obs["kind"], obs["media_type"]])[:32]
                if not self.store.get("representation", rep_id, optional=True):
                    self.store.put("representation", {"id": rep_id, "version_id": version_id,
                        "source_id": source["id"], "location": obs["location"], "kind": obs["kind"], "media_type": obs["media_type"]})
                placement_id = "placement_" + digest([source["id"], obs["resource_id"], obs["location"], obs["parent"], obs["title"]])[:32]
                self.store.put("placement", {"id": placement_id, "source_id": source["id"], "resource_id": obs["resource_id"],
                    "location": obs["location"], "parent": obs["parent"], "label": obs["title"], "relation": obs["relation"]})
                obs.update(version_id=version_id, representation_id=rep_id, placement_id=placement_id)
            snapshot = {"id": snapshot_id, "source_id": source["id"], "scope_id": run["scope_id"], "run_id": run["id"],
                "complete": complete, "completed_at": now(), "observations": observations, "gaps": run["gaps"],
                "unexplored_count": len(run["frontier"]), "capabilities": run["capabilities"],
                "usage": {k: run[k] for k in ("actions", "bytes_read", "seconds")}}
            self.store.put("snapshot", snapshot)
            old = self.store.get("snapshot", run["baseline"], optional=True) if run["baseline"] else None
            comparison = {"id": uid("comparison"), "source_id": source["id"], "previous": run["baseline"],
                "current": snapshot_id, **compare(old, snapshot, old is None or old["scope_id"] == run["scope_id"])}
            self.store.put("comparison", comparison)
            if complete:
                # A newer concurrent successful scan must not be replaced by an older one.
                if source["baseline"] != run["baseline"]:
                    run["message"] = "Another successful scan advanced the baseline; this snapshot is retained without replacing it."
                else:
                    source["baseline"], source["last_success"] = snapshot_id, snapshot["completed_at"]
            source["availability"] = "ACCESSIBLE" if complete else "PARTIAL"
            source["availability_at"] = now()
            self.store.put("source", source)
            run.update(state="COMPLETE" if complete else "INCOMPLETE", snapshot_id=snapshot_id)
            self.store.put("run", run)
            self.store.event("discovery_finished", {"run_id": run["id"], "snapshot_id": snapshot_id, "complete": complete})
        return run

    def index(self, snapshot_ids=None):
        snapshots = ([self.store.get("snapshot", i) for i in snapshot_ids] if snapshot_ids is not None
                     else [self.store.get("snapshot", s["baseline"]) for s in self.store.all("source") if s["baseline"]])
        caps = capabilities(self.browser)
        rows = []
        for snapshot in snapshots:
            source = self.store.get("source", snapshot["source_id"])
            for obs in snapshot["observations"]:
                methods = self.adapters[source["kind"]].methods(obs)
                row = {**obs, "snapshot_id": snapshot["id"], "source_id": source["id"], "source_name": source["name"],
                       "scope_id": snapshot["scope_id"], "feasibility": feasibility(obs, methods, caps["converters"])}
                row["runtime_limitations"] = caps["browser"].get("limitations", []) if source["kind"] == "web" else []
                if not caps["transcription_model_available"]:
                    row["feasibility"]["transcription"] = "UNAVAILABLE"
                row["selection_id"] = digest([snapshot["id"], obs["representation_id"], obs["placement_id"]])
                rows.append(row)
        return {"resources": rows, "coverage": [{"source_id": s["source_id"], "complete": s["complete"], "gaps": s["gaps"]} for s in snapshots],
                "estimate": estimate(rows), "message": "Index-only is a completed workflow. Select resources and intended derivatives before capture."}

    def prepare_capture(self, snapshot_ids, selection_ids, *, derivatives=None, max_bytes=1024**3, max_seconds=3600, max_attempts=3):
        if not 1 <= max_attempts <= 10 or max_bytes <= 0 or max_seconds <= 0:
            raise CaptureError("Provide positive capture budgets and 1–10 attempts per item.")
        index = self.index(snapshot_ids)
        selected = set(selection_ids)
        items = [row for row in index["resources"] if row["selection_id"] in selected]
        if not items or selected != {i["selection_id"] for i in items}:
            raise CaptureError("Choose resources from the reviewed index.")
        if any(item["external"] for item in items):
            raise CaptureError("External candidates require explicit source scope admission before capture.")
        derivatives = derivatives or []
        if any(d not in ("text", "transcript") for d in derivatives):
            raise CaptureError("Supported optional derivations are text and transcript.")
        from .derivation import supported
        for item in items:
            item["derivation_feasibility"] = {d: "READY" if supported(item["media_type"], d) else "UNAVAILABLE" for d in derivatives}
        sources = list({i["source_id"]: {"source_id": i["source_id"], "scope_id": i["scope_id"]} for i in items}.values())
        return self._review("capture", {"items": items, "sources": sources, "derivatives": derivatives,
            "max_bytes": max_bytes, "max_seconds": max_seconds, "max_attempts": max_attempts,
            "processing_destination": "local", "estimate": estimate(items),
            "note": "Dependencies not selected remain links. Earlier versions and their derivatives are retained."})

    def capture(self, review_id, steps=10):
        from .capture import execute
        from .persistence import project_lock
        with project_lock(self.store.root):
            return execute(self, self._approved(review_id, "capture"), min(max(1, steps), 100))

    def cancel_job(self, job_id):
        job = self.store.get("job", job_id)
        job["state"] = "CANCELLED"
        with self.store.transaction():
            self.store.put("job", job)
        return job

    def report(self):
        return {"project": self.store.get("project", "project"), "sources": self.store.all("source"),
                "comparisons": self.store.all("comparison"), "jobs": self.store.all("job"),
                "artefacts": self.store.all("artefact"), "verification": self.store.all("verification")}
