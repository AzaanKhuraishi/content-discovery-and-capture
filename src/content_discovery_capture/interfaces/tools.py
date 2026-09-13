"""Narrow tool facade. User consent cannot be synthesized through this interface."""
from ..domain import CaptureError
from ..packaging import export_pack
from ..capture import recover


TOOLS = [
    ("configure_host_browser", "Advertise only browser capabilities actually provided by this host; no sessions are persisted.", {"offered": "object"}),
    ("runtime_actions", "Read pending scoped browser actions for the host to execute with its own tools.", {}),
    ("submit_runtime_result", "Return bounded observations or staged capture results for a pending browser action. Does not grant approval.",
        {"action_id": "string", "result": "object"}),
    ("registry", "List registered sources before any refresh. Also offers adding a new location.", {}),
    ("classify", "Record evidence-based content-purpose labels without supersession or relevance judgments.",
        {"resource_id": "string", "labels": "array", "basis": "string", "evidence": "string"}),
    ("register_source", "Register an explicitly supplied source and scope; does not scan.",
        {"name": "string", "kind": "string", "location": "string", "scope": "object", "access_requirement": "string"}),
    ("update_source", "Rename, disable/remove or revise scope; captured history is retained.",
        {"source_id": "string", "state": "string", "name": "string", "scope": "object"}),
    ("prepare_refresh", "Show source selection and discovery budgets for user review. No source I/O.",
        {"source_ids": "array", "budget": "object"}),
    ("refresh", "Run bounded discovery only after user review; call again to continue a running refresh.",
        {"review_id": "string", "steps": "integer"}),
    ("resume_run", "Resume a checkpoint after the user restores access.", {"run_id": "string", "steps": "integer"}),
    ("prepare_discovery_resume", "Review a larger total discovery budget while retaining checkpoints.", {"run_id": "string", "budget": "object"}),
    ("prepare_capture_resume", "Review renewed capture totals without resetting history or attempts.",
        {"job_id": "string", "max_bytes": "integer", "max_seconds": "integer", "max_attempts": "integer"}),
    ("close_run", "Close discovery as incomplete, preserving the previous successful baseline.", {"run_id": "string"}),
    ("index", "Show pre-capture inventory, coverage and feasibility. Index-only is complete.", {"snapshot_ids": "array"}),
    ("prepare_capture", "Prepare selected representations and optional derivatives for user approval.",
        {"snapshot_ids": "array", "selection_ids": "array", "derivatives": "array", "max_bytes": "integer", "max_seconds": "integer", "max_attempts": "integer"}),
    ("capture", "Capture only an approved plan, preserving versions and using bounded attempts.", {"review_id": "string", "steps": "integer"}),
    ("cancel_job", "Cancel further processing and preserve existing outputs.", {"job_id": "string"}),
    ("resume_status", "Report saved work and whether to offer a source-selection refresh prompt.", {}),
    ("set_resume_prompt", "Enable or disable offering refresh on project resume; never scans automatically.", {"enabled": "boolean"}),
    ("report", "Read source-specific comparisons, captured versions and quality outcomes.", {}),
    ("export_pack", "Build and verify a local Source Pack from explicitly selected captured artefacts.",
        {"artefact_ids": "array", "include_originals": "boolean"}),
    ("prepare_compendium", "Review combining selected captured text versions into one reading copy.",
        {"artefact_ids": "array", "title": "string", "max_bytes": "integer", "max_seconds": "integer"}),
    ("build_compendium", "Build an approved compendium while retaining all original inputs and lineage.", {"review_id": "string"}),
    ("recover", "Reconcile interrupted capture attempts; retain spent attempts and immutable history.", {}),
]

REQUIRED = {"register_source": ["name", "kind", "location"], "update_source": ["source_id"], "refresh": ["review_id"],
            "classify": ["resource_id", "labels", "basis", "evidence"],
            "configure_host_browser": ["offered"], "submit_runtime_result": ["action_id", "result"],
            "prepare_compendium": ["artefact_ids", "title"], "build_compendium": ["review_id"],
            "prepare_discovery_resume": ["run_id", "budget"], "prepare_capture_resume": ["job_id", "max_bytes", "max_seconds", "max_attempts"],
            "resume_run": ["run_id"], "close_run": ["run_id"], "prepare_capture": ["snapshot_ids", "selection_ids"],
            "capture": ["review_id"], "cancel_job": ["job_id"], "set_resume_prompt": ["enabled"]}


def definitions():
    return [{"name": name, "description": desc, "inputSchema": {"type": "object", "properties": {
        key: ({"type": kind, "items": {"type": "string"}} if kind == "array" else {"type": kind}) for key, kind in fields.items()},
        "required": REQUIRED.get(name, []), "additionalProperties": False}} for name, desc, fields in TOOLS]


def invoke(app, name, arguments, panel=None):
    definition = next((d for d in definitions() if d["name"] == name), None)
    if definition is None:
        raise CaptureError("Unknown tool. Approval is available only through the user review surface.")
    schema = definition["inputSchema"]
    if set(arguments) - schema["properties"].keys() or set(schema["required"]) - arguments.keys():
        raise CaptureError("Arguments do not match the tool contract.")
    types = {"string": str, "integer": int, "object": dict, "array": list, "boolean": bool}
    for key, value in arguments.items():
        if type(value) is not types[schema["properties"][key]["type"]]:
            raise CaptureError("Argument type does not match the tool contract.")
    if name == "recover":
        from ..persistence import project_lock
        with project_lock(app.store.root):
            result = recover(app)
    elif name in ("runtime_actions", "submit_runtime_result"):
        from ..runtimes.host import pending, supply
        result = pending(app) if name == "runtime_actions" else supply(app, **arguments)
    elif name in ("prepare_compendium", "build_compendium"):
        from ..compendium import prepare, execute
        result = prepare(app, **arguments) if name == "prepare_compendium" else execute(app, **arguments)
    else:
        result = export_pack(app, **arguments) if name == "export_pack" else recover(app) if name == "recover" else getattr(app, name)(**arguments)
    if name.startswith("prepare_") and panel:
        result = {**result, "review_url": panel.url + "/review/" + result["id"],
                  "next_action": "Show this review link to the user. Do not approve or click it on their behalf."}
    return result
