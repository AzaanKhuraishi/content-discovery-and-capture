"""Allowlist-only export. No project-directory recursive archiving."""
from collections import defaultdict
from pathlib import Path
import json
import mimetypes
import os
import re
import shutil
import tempfile
import zipfile

from .domain import CaptureError, digest, now, safe_data, safe_text, uid


def organisation(artefacts, store=None):
    by_hash, by_name = defaultdict(list), defaultdict(list)
    for a in artefacts:
        by_hash[a["sha256"]].append(a["id"])
        by_name[a["title"].casefold()].append(a)
    probable = []
    if store:
        signatures = []
        for a in artefacts[:500]:
            if a["media_type"].startswith("text/") and a["size"] <= 256 * 1024:
                words = re.findall(r"\w+", store.object_path(a["sha256"]).read_text(errors="replace").casefold())[:20000]
                shingles = {tuple(words[i:i+5]) for i in range(max(0, len(words)-4))}
                if shingles:
                    signatures.append((a, shingles))
        for i, (left, a) in enumerate(signatures):
            for right, b in signatures[i+1:]:
                if left["sha256"] != right["sha256"]:
                    similarity = len(a & b) / len(a | b)
                    if similarity >= .85:
                        probable.append({"artefact_ids": [left["id"], right["id"]], "similarity": round(similarity, 4),
                                         "method": "five-word-shingle Jaccard", "status": "PROBABLE_DUPLICATE"})
    return {"exact_duplicates": [{"sha256": key, "artefact_ids": values} for key, values in by_hash.items() if len(values) > 1],
        "name_conflicts": [{"name": key, "artefact_ids": [v["id"] for v in values]} for key, values in by_name.items()
                           if len({v["sha256"] for v in values}) > 1],
        "probable_duplicates": probable, "probable_duplicate_note": "Bounded textual similarity only (first 500 artefacts, 256 KiB each); no semantic identity or deletion conclusion.",
        "classifications": store.all("classification") if store else [],
        "categories": {a["id"]: "transcripts" if a.get("derivation") == "transcript" else "reading-copies" if a["role"] == "derived"
                       else a["media_type"].split("/")[0] if a["media_type"].startswith(("image/", "audio/", "video/", "text/"))
                       else "unclassified" for a in artefacts}}


def export_pack(app, artefact_ids=None, include_originals=True):
    store = app.store
    all_artefacts = store.all("artefact")
    requested = set(artefact_ids) if artefact_ids is not None else {a["id"] for a in all_artefacts}
    if requested - {a["id"] for a in all_artefacts}:
        raise CaptureError("Unknown artefact in package selection.")
    selected = [a for a in all_artefacts if a["id"] in requested and (include_originals or a["role"] != "original")]
    if not selected:
        raise CaptureError("No captured artefacts match this package selection.")
    export_id = uid("pack")
    target_root = store.root / "exports"
    if target_root.is_symlink():
        raise CaptureError("Export directory cannot be a symbolic link.")
    target_root.mkdir(exist_ok=True, mode=0o700)
    target_root.chmod(0o700)
    stage = Path(tempfile.mkdtemp(prefix="pack-", dir=store.staging))
    entries, stored = [], {}
    for artefact in selected:
        source = store.object_path(artefact["sha256"])
        if not source.is_file() or store.hash_file(source) != artefact["sha256"]:
            raise CaptureError("An included artefact failed integrity checking.")
        if artefact["media_type"].startswith("text/") or artefact["media_type"] in ("application/json", "application/xhtml+xml"):
            # Originals stay private and untouched. Do not silently sanitise an exported original.
            with source.open(errors="replace") as stream:
                carry = ""
                while chunk := stream.read(256 * 1024):
                    content = carry + chunk
                    if safe_text(content) != content:
                        raise CaptureError("Selected text contains credential-like access data. Preserve the private original and export a sanitised reading derivative instead.")
                    carry = content[-2048:]
        # Content-addressed payload names avoid filename collisions and duplicate physical bytes.
        key = (artefact["role"], artefact["sha256"])
        if key not in stored:
            folder = "derived" if artefact["role"] == "derived" else "snapshots" if artefact["role"] == "snapshot" else "originals"
            extension = mimetypes.guess_extension(artefact["media_type"]) or ".bin"
            relative = f"{folder}/{artefact['sha256']}{extension}"
            path = stage / relative
            path.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, path)
            stored[key] = relative
        entries.append({**artefact, "path": stored[key]})
    selected_ids = {a["id"] for a in selected}
    provenance = []
    for record in store.all("provenance"):
        if record["artefact_id"] not in selected_ids:
            continue
        record = dict(record)
        if record.get("location", "").startswith("/"):
            source = store.get("source", record["source_id"])
            try:
                relative = str(Path(record["location"]).relative_to(source["location"]))
            except ValueError:
                relative = Path(record["location"]).name
            record["location"] = "source:" + source["id"] + "/" + relative
        provenance.append(safe_data(record))
    excluded = [{"artefact_id": a["id"], "title": a["title"], "sha256": a["sha256"],
                 "reason": "Originals omitted by export choice" if not include_originals and a["role"] == "original" else "Not selected"}
                for a in all_artefacts if a["id"] not in selected_ids]
    manifest = {"id": export_id, "schema_version": 1, "created_at": now(), "artefacts": entries,
        "provenance": provenance, "verification": [v for v in store.all("verification") if v["artefact_id"] in selected_ids],
        "exclusions": excluded, "organisation": organisation(selected, store),
        "sources": [{"id": s["id"], "name": s["name"], "kind": s["kind"]} for s in store.all("source")],
        "gaps": [{"source_id": s["source_id"], "complete": s["complete"], "gaps": [g.get("reason", "Unknown gap") for g in s["gaps"]]}
                 for s in store.all("snapshot")],
        "note": "Source versions retain their identities. No relevance, supersession or redistribution-rights conclusion is implied."}
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    lines = ["# Source Pack", "", "Captured material, versions and limitations are recorded in manifest.json.", "",
             "Earlier versions are retained without relevance or supersession judgments.", "", "## Contents", ""]
    for entry in entries:
        label = entry["title"].replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
        lines.append(f"- [{label}]({entry['path']}) — {entry['role']}; version `{entry['version_id']}`")
    lines += ["", "## Exclusions", ""] + [f"- {e['title']}: {e['reason']}" for e in excluded]
    lines += ["", "Embedded links may require the original source. Only payloads listed above are included."]
    (stage / "INDEX.md").write_text("\n".join(lines) + "\n")
    members = sorted(p for p in stage.rglob("*") if p.is_file())
    (stage / "MANIFEST.sha256").write_text("".join(f"{store.hash_file(p)}  {p.relative_to(stage).as_posix()}\n" for p in members))
    members.append(stage / "MANIFEST.sha256")
    archive_path = target_root / (export_id + ".zip")
    partial = archive_path.with_suffix(".partial")
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in members:
            archive.write(path, path.relative_to(stage).as_posix())
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip() or set(archive.namelist()) != {p.relative_to(stage).as_posix() for p in members}:
            raise CaptureError("Package verification failed.")
        for path in members:
            import hashlib
            if hashlib.sha256(archive.read(path.relative_to(stage).as_posix())).hexdigest() != store.hash_file(path):
                raise CaptureError("Package member hash mismatch.")
    os.replace(partial, archive_path)
    archive_path.chmod(0o600)
    with store.transaction():
        store.put("manifest", manifest)
        store.event("pack_created", {"id": export_id, "sha256": store.hash_file(archive_path), "members": len(members)})
    shutil.rmtree(stage)
    return {"path": str(archive_path), "sha256": store.hash_file(archive_path), "members": len(members), "manifest_id": export_id}
