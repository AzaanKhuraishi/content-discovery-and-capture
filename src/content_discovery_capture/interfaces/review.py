"""Loopback-only user consent surface. No model-facing approval tool exists.

This separates consent from the ordinary tool API; it is not a sandbox against a
malicious process with the user's filesystem/browser privileges.
"""
from html import escape
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit
import secrets
import threading

from ..domain import CaptureError, digest, now
from ..persistence import Store


def decide(store, review_id, approved, selected=None):
    """Trusted UI entry point. Deliberately absent from CLI and tool dispatch."""
    with store.transaction():
        review = store.get("review", review_id)
        if review["state"] != "PENDING":
            raise CaptureError("This review has already been decided.")
        if digest(review["payload"]) != review["payload_hash"]:
            raise CaptureError("The reviewed plan has changed.")
        if approved and selected is not None:
            field, key = ("sources", "source_id") if review["kind"] == "discovery" else ("items", "selection_id")
            offered = {item[key] for item in review["payload"][field]}
            if not selected or set(selected) - offered:
                raise CaptureError("Choose at least one item from the displayed plan.")
            if review["payload"].get("resume_job_id") and set(selected) != offered:
                raise CaptureError("Budget renewal preserves the existing job selection. Cancel and prepare a separate plan to change selection.")
            review["payload"][field] = [i for i in review["payload"][field] if i[key] in selected]
            if field == "items":
                included = {i["source_id"] for i in review["payload"][field]}
                review["payload"]["sources"] = [s for s in review["payload"]["sources"] if s["source_id"] in included]
            review["payload_hash"] = digest(review["payload"])
        review.update(state="APPROVED" if approved else "CANCELLED", decided_at=now(), decision_origin="local-user-review")
        store.put("review", review)
        store.event("user_decision", {"review_id": review_id, "state": review["state"], "payload_hash": review["payload_hash"]})
    return review


class ReviewServer:
    def __init__(self, project_root, port=0):
        self.root = project_root
        self.nonces = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, body):
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body.encode())

            def valid_host(self):
                return self.headers.get("Host") == f"127.0.0.1:{owner.http.server_port}"

            def do_GET(self):
                if not self.valid_host():
                    self.respond(403, "Invalid host")
                    return
                identity = urlsplit(self.path).path.removeprefix("/review/")
                store = Store(owner.root)
                try:
                    review = store.get("review", identity)
                    if review["state"] != "PENDING":
                        self.respond(200, f"<p>This request is {escape(review['state'].lower())}. Return to your conversation.</p>")
                        return
                    nonce = secrets.token_urlsafe(32)
                    owner.nonces[identity] = nonce
                    payload = review["payload"]
                    discovery = review["kind"] == "discovery"
                    title = "Choose where to check" if discovery else "Choose what to capture"
                    rows = []
                    for item in payload["sources" if discovery else "items"]:
                        key = item["source_id" if discovery else "selection_id"]
                        label = item["name" if discovery else "title"]
                        detail = item.get("location", "")
                        if discovery:
                            scope = item["scope"]
                            detail += " · Check within: " + ", ".join(scope["roots"])
                            detail += " · Maximum depth: " + str(scope["max_depth"])
                            if scope["excludes"]:
                                detail += " · Exclude: " + ", ".join(scope["excludes"])
                            if scope["asset_origins"]:
                                detail += " · Linked asset locations: " + ", ".join(scope["asset_origins"])
                            if scope["allow_private_network"]:
                                detail += " · Local/private network access included"
                        if not discovery:
                            detail += " · " + item["feasibility"]["status"]
                            detail += " · derivatives: " + ", ".join(f"{key}: {value}" for key, value in item.get("derivation_feasibility", {}).items())
                            if item.get("runtime_limitations"):
                                detail += " · " + "; ".join(item["runtime_limitations"])
                        rows.append(f'<label><input type="checkbox" name="selected" value="{escape(key)}" checked> '
                                    f'{escape(label)}<small>{escape(detail)}</small></label>')
                    if discovery:
                        limits = payload["budget_per_source"]
                        summary = f"Per source: up to {limits['max_items']:,} items, {limits['max_bytes'] / 1024**2:.1f} MB inspected, {limits['max_seconds']} seconds and {limits['max_actions']} actions. No capture."
                        if payload.get("warnings"):
                            summary += " Warnings: " + " ".join(payload["warnings"])
                    else:
                        summary = f"Up to {payload['max_bytes'] / 1024**2:.1f} MB processed, {payload['max_seconds']} seconds, {payload['max_attempts']} attempts per item. Processing stays local."
                        summary += " Derivatives: " + (", ".join(payload["derivatives"]) or "none") + ". Previous versions remain preserved."
                    self.respond(200, '<!doctype html><meta charset="utf-8"><title>Content Discovery and Capture</title>'
                        '<style>body{font:17px system-ui;max-width:800px;margin:50px auto;padding:24px;background:#f7f8fa;color:#182333}'
                        'label{display:block;padding:16px;margin:8px 0;background:white;border:1px solid #dce1e8;border-radius:10px}'
                        'small{display:block;margin-left:24px;color:#526071;overflow-wrap:anywhere}button{padding:12px 18px;margin:12px 8px 0 0;font:inherit}'
                        '</style><h1>' + title + '</h1><p>' + escape(summary) + '</p>'
                        '<p>To add a location or change scope, return to your conversation before approving.</p>'
                        f'<form method="post" action="/review/{escape(identity)}"><input type="hidden" name="nonce" value="{nonce}">'
                        + "".join(rows) + '<button name="decision" value="approve">Approve selected</button>'
                        '<button name="decision" value="cancel">Cancel</button></form>')
                except CaptureError:
                    self.respond(404, "Review not found")
                finally:
                    store.close()

            def do_POST(self):
                if not self.valid_host() or self.headers.get("Origin") != owner.url:
                    self.respond(403, "Invalid review origin")
                    return
                length = int(self.headers.get("Content-Length", 0))
                if length > 1000000:
                    self.respond(413, "Review too large")
                    return
                data = parse_qs(self.rfile.read(length).decode())
                identity = urlsplit(self.path).path.removeprefix("/review/")
                nonce = data.get("nonce", [""])[0]
                if not nonce or not secrets.compare_digest(nonce, owner.nonces.get(identity, "")):
                    self.respond(403, "Invalid review form")
                    return
                store = Store(owner.root)
                try:
                    decide(store, identity, data.get("decision") == ["approve"], data.get("selected", []))
                    owner.nonces.pop(identity, None)
                    self.respond(200, "<p>Decision saved. Return to your conversation to continue.</p>")
                except CaptureError as error:
                    self.respond(409, escape(str(error)))
                finally:
                    store.close()

        self.http = HTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_port}"

    def start(self):
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
