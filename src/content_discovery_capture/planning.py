"""Pure comparison and estimation functions. No source access or model calls."""
from collections import Counter
from .domain import digest


def version_evidence(obs):
    return {"content_hash": obs.get("content_hash"), "size": obs.get("size"),
            "media_type": obs.get("media_type"), "metadata": {key: value for key, value in obs.get("metadata", {}).items()
            if key in ("mtime_ns", "ctime_ns", "etag", "modified", "source_revision")}}


def compare(previous, current, compatible=True):
    old = {o["resource_id"]: o for o in (previous or {}).get("observations", []) if not o.get("external")}
    new = {o["resource_id"]: o for o in current["observations"] if not o.get("external")}
    findings = []
    complete = current["complete"]
    for identity, item in new.items():
        before = old.get(identity)
        statuses, evidence = [], []
        if before is None:
            statuses.append("NEW")
            evidence.append("First observed" if compatible else "Newly in scope; publication time unknown")
        else:
            if (item["location"], item["title"]) != (before["location"], before["title"]):
                statuses.append("MOVED/RENAMED")
            h1, h2 = before.get("content_hash"), item.get("content_hash")
            if h1 and h2:
                statuses.append("UNCHANGED" if h1 == h2 else "CONFIRMED_CHANGED")
                evidence.append("Full content SHA-256 comparison")
            elif version_evidence(before) != version_evidence(item):
                statuses.append("POSSIBLY_CHANGED")
                evidence.append("Metadata changed; content equality not established")
            elif any(v is not None for v in version_evidence(item)["metadata"].values()) or item.get("size") is not None:
                statuses.append("UNCHANGED")
                evidence.append("Observed metadata unchanged; not a full content comparison")
            else:
                statuses.append("UNKNOWN/SCAN-INCONCLUSIVE")
                evidence.append("No reliable change indicator")
        findings.append({"resource_id": identity, "title": item["title"], "statuses": statuses, "evidence": evidence})
    for identity in old.keys() - new.keys():
        status = "MISSING" if complete and compatible else "UNKNOWN/SCAN-INCONCLUSIVE"
        findings.append({"resource_id": identity, "title": old[identity]["title"], "statuses": [status],
                         "evidence": ["Not observed; this is not a deletion claim" if compatible else "Scope changed; absence is not comparable"]})
    return {"findings": findings, "counts": dict(Counter(s for f in findings for s in f["statuses"])),
            "count_note": "Change dimensions overlap; counts are not additive resource totals."}


def estimate(observations, operation="capture"):
    known = sum(o.get("size") or 0 for o in observations)
    unknown = sum(o.get("size") is None for o in observations)
    count = len(observations)
    rating = "VERY HIGH" if known > 2**30 or count > 2000 else "HIGH" if known > 100 * 2**20 or count > 200 else "MODERATE" if count > 20 else "LOW"
    return {"operation": operation, "resources": count, "known_bytes": known, "unknown_sizes": unknown,
            "workload": rating, "tokens": None, "time_seconds": None,
            "limitations": ["Metadata estimate, not guaranteed consumption", "Unknown sizes and media duration may increase workload"]}


def feasibility(observation, methods, converters):
    if not methods:
        status, reason = "UNAVAILABLE", "No permitted method in this scope/runtime"
    elif observation.get("metadata", {}).get("reference_only"):
        status, reason = "UNCERTAIN", "Reference discovered; access not established"
    elif observation.get("needs_resolution") or observation.get("metadata", {}).get("dynamic"):
        status, reason = ("MULTI-METHOD" if len(methods) > 1 else "UNCERTAIN"), "Protected or dynamic representation"
    else:
        status, reason = "READY", "Supported retrieval method; final verification still required"
    return {"status": status, "reason": reason, "methods": methods, "original_capture": status,
            "transcription": "READY" if converters.get("transcript") else "UNAVAILABLE"}
