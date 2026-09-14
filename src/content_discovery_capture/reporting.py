"""Versioned manifests are produced even when the user does not request a ZIP."""
import json
from .domain import CaptureError, now, uid
from .packaging import organisation


def create_manifest(app):
    store = app.store
    artefacts = store.all("artefact")
    record = {"id": uid("manifest"), "schema_version": 1, "created_at": now(),
              "artefacts": artefacts, "sources": store.all("source"), "versions": store.all("version"),
              "placements": store.all("placement"), "representations": store.all("representation"),
              "provenance": store.all("provenance"), "verification": store.all("verification"),
              "organisation": organisation(artefacts, store), "jobs": store.all("job"),
              "snapshot_ids": [s["id"] for s in store.all("snapshot")],
              "note": "Private project manifest. Use Source Pack export for distributable provenance."}
    with store.transaction():
        store.put("manifest", record)
    root = store.root / "reports"
    if root.is_symlink():
        raise CaptureError("Report directory cannot be a symbolic link.")
    root.mkdir(exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / (record["id"] + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    lines = ["# Capture report", "", "Originals, versions and all derivatives remain preserved.", ""]
    for job in record["jobs"]:
        lines.append(f"- {job['id']}: {job['state']}")
    lines += ["", "## Captured artefacts", ""]
    for a in artefacts:
        title = a["title"].replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- [{title}](../objects/{a['sha256']}) — {a['role']}; version {a['version_id']}")
    markdown = root / (record["id"] + ".md")
    markdown.write_text("\n".join(lines) + "\n")
    markdown.chmod(0o600)
    return record
