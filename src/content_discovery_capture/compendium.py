"""An explicitly selected multi-input derivative with complete version lineage."""
import time
from .domain import CaptureError, digest, now, uid
from .verification import verify


def prepare(app, artefact_ids, title, max_bytes=10*1024*1024, max_seconds=60):
    if not artefact_ids or max_bytes <= 0 or max_seconds <= 0:
        raise CaptureError("Select captured reading copies and positive processing limits.")
    items = []
    for identity in dict.fromkeys(artefact_ids):
        a = app.store.get("artefact", identity)
        if not a["media_type"].startswith("text/"):
            raise CaptureError("A compendium combines captured text or reading copies. Select an appropriate derivative for other formats.")
        items.append({"selection_id": a["id"], "title": a["title"], "source_id": a["source_id"],
                      "size": a["size"], "sha256": a["sha256"], "feasibility": {"status": "READY"}})
    return app._review("compendium", {"items": items, "title": title, "sources": [], "derivatives": ["compendium"],
        "max_bytes": max_bytes, "max_seconds": max_seconds, "max_attempts": 1,
        "note": "Combine selected captured versions; originals and earlier derivatives remain unchanged."})


def execute(app, review_id):
    review = app._approved(review_id, "compendium")
    existing = next((a for a in app.store.all("artefact") if a.get("review_id") == review_id), None)
    if existing:
        return existing
    payload, started = review["payload"], time.monotonic()
    lines, inputs, size = ["# " + payload["title"], ""], [], 0
    for item in payload["items"]:
        a = app.store.get("artefact", item["selection_id"])
        path = app.store.object_path(a["sha256"])
        if app.store.hash_file(path) != a["sha256"]:
            raise CaptureError("An input failed integrity verification.")
        size += a["size"]
        if size > payload["max_bytes"] or time.monotonic() - started > payload["max_seconds"]:
            raise CaptureError("Compendium reached its approved budget.")
        text = path.read_text(errors="replace")
        import re
        text = re.sub(r"(?m)^(#{1,6}) ", lambda m: "#" * min(6, len(m[1]) + 2) + " ", text)
        lines += ["## " + a["title"], "", f"Source version: {a['version_id']} · SHA-256: {a['sha256']}", "", text, ""]
        inputs.append({"artefact_id": a["id"], "version_id": a["version_id"], "sha256": a["sha256"]})
    destination = app.store.temp()
    destination.write_text("\n".join(lines))
    if destination.stat().st_size + size > payload["max_bytes"]:
        raise CaptureError("Compendium input and output exceed the approved byte budget.")
    verification = verify(destination, "text/markdown", "text/markdown", "derived")
    obj = app.store.promote(destination)
    record = {"id": uid("artefact"), "review_id": review_id, "title": payload["title"], "role": "derived", "derivation": "compendium",
        "inputs": inputs, "version_id": "composite_" + digest(inputs)[:32], "source_id": "multiple", "resource_id": "composite",
        "media_type": "text/markdown", "created_at": now(), "method_version": "1", **obj,
        "limitations": ["Combined reading copy; source version boundaries and hashes are retained"]}
    with app.store.transaction():
        app.store.put("artefact", record)
        app.store.put("verification", {"id": uid("verification"), "artefact_id": record["id"], "sha256": obj["sha256"], "verified_at": now(), **verification})
        app.store.put("provenance", {"id": uid("provenance"), "artefact_id": record["id"], "inputs": inputs, "created_at": now()})
    from .reporting import create_manifest
    create_manifest(app)
    return record
