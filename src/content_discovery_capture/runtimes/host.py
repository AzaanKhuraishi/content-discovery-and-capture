"""Typed action/result bridge for agent hosts, including Codex.

The host executes its own browser tools. The core never assumes access to an
extension session, imports a host SDK, or accepts approval through this bridge.
"""
from pathlib import Path
from ..domain import AccessRequired, RuntimeActionRequired, CaptureError, CaptureResult, Scope, digest, safe_text, safe_url, now
from .contract import WebResponse


class HostBrowserProvider:
    def __init__(self, store, capabilities):
        self.store = store
        self.offered = {key: bool(capabilities.get(key)) for key in ("inspect", "snapshot", "download")}
        self.context = "unbound"

    def capabilities(self):
        return {**self.offered, "provider": "host-action-bridge", "authenticated_context": "host-owned; never persisted",
                "limitations": ["Coverage depends on the host's reported observations", "Browser transfer bytes may exceed measurable inspection bytes", "Rendered snapshots retain an observation, not the server's original response"]}

    def bind_context(self, identity):
        self.context = identity

    def _request(self, operation, location, scope, max_bytes, max_actions=1):
        if safe_url(location) != location:
            raise AccessRequired("The host must resolve access through the stable source; signed URLs cannot be persisted in action requests.")
        request = {"operation": operation, "location": location, "scope": scope.data(), "max_bytes": max_bytes,
                   "max_actions": max_actions, "context": self.context}
        identity = "action_" + digest({k: v for k, v in request.items() if k not in ("max_bytes", "max_actions")})[:32]
        existing = self.store.get("runtime_action", identity, optional=True)
        if existing and existing["state"] == "DONE":
            return existing
        if existing and existing["state"] == "FAILED":
            raise CaptureError(existing["message"])
        if existing is None:
            with self.store.transaction():
                self.store.put("runtime_action", {"id": identity, "state": "PENDING", "request": request,
                    "destination": str(self.store.staging / identity), "created_at": now()})
        raise RuntimeActionRequired("A browser action is waiting for the conversational runtime. Execute the pending scoped action, then resume.")

    def inspect(self, location, scope, max_bytes, max_actions):
        if not self.offered["inspect"]:
            raise CaptureError("The host has no browser inspection capability.")
        action = self._request("inspect", location, scope, max_bytes, max_actions)
        result = action["result"]
        return WebResponse(result["html"].encode(), result["location"], {"content-type": "text/html"},
                           complete=result["complete"], limitations=result["limitations"], actions=result["actions"], method="browser")

    def capture(self, location, scope, destination, max_bytes, timeout, method):
        required = "snapshot" if method == "browser-snapshot" else "download"
        if not self.offered[required]:
            raise CaptureError("The host does not advertise this capture capability.")
        action = self._request(method, location, scope, max_bytes)
        path = Path(action["destination"])
        if not path.is_file() or path.is_symlink():
            raise CaptureError("The host capture file is missing or invalid.")
        path.replace(destination)
        return CaptureResult(destination, action["result"]["media_type"], "snapshot" if required == "snapshot" else "original",
                             limitations=action["result"].get("limitations", []))


def pending(app):
    return [a for a in app.store.all("runtime_action") if a["state"] == "PENDING"]


def supply(app, action_id, result):
    action = app.store.get("runtime_action", action_id)
    if action["state"] != "PENDING":
        raise CaptureError("This runtime action has already been answered.")
    request = action["request"]
    if result.get("error"):
        action.update(state="FAILED", message=safe_text(str(result["error"])))
    else:
        location = safe_url(result.get("location", request["location"]))
        if not Scope.from_dict(request["scope"]).permits(location, "web", asset=True):
            raise CaptureError("Runtime result is outside the requested scope.")
        if request["operation"] == "inspect":
            html = safe_text(str(result.get("html", "")))
            if len(html.encode()) > request["max_bytes"]:
                raise CaptureError("Runtime observation exceeds its approved inspection budget.")
            action["result"] = {"html": html, "location": location, "complete": result.get("complete") is True,
                                "actions": max(1, int(result.get("actions", 1))),
                                "limitations": ["Sanitised host browser observation", *[safe_text(str(x)) for x in result.get("limitations", [])]]}
            if action["result"]["actions"] > request["max_actions"]:
                raise CaptureError("Runtime exceeded the approved interaction budget.")
        else:
            path = Path(action["destination"])
            if not path.is_file() or path.is_symlink() or path.stat().st_size > request["max_bytes"]:
                raise CaptureError("Save the requested capture at its designated staging path within the byte limit.")
            action["result"] = {"location": location, "media_type": str(result.get("media_type", "application/octet-stream")),
                                "limitations": [safe_text(str(x)) for x in result.get("limitations", [])]}
        action["state"] = "DONE"
    with app.store.transaction():
        app.store.put("runtime_action", action)
    return {"id": action_id, "state": action["state"]}
