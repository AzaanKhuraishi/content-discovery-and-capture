# Adapter and host contracts

V1 registers exactly two source kinds: `web` and `filesystem`. Browser providers implement access capabilities for Web; they are not additional source adapters. The core has no Codex SDK dependency and does not assume that one MCP server can call another server's tools.

## Source contract

`sources/contract.py` defines the structural Python Protocol:

| Operation | Input | Output and obligation |
| --- | --- | --- |
| `discover` | Source, immutable Scope, durable cursor, remaining Budget | DiscoveryBatch containing observations, next frontier, explicit gaps and measured bytes/actions. Metadata inspection only; no capture objects. |
| `methods` | Reviewed observation | Ordered eligible method names. An empty list means UNAVAILABLE. |
| `capture` | Selected observation, approved Scope/method, staging destination, remaining bytes/seconds | CaptureResult with actual media type, representation role, access time and limitations. Write only the designated staging file. |

Observations distinguish native identity, stable location, title, media type, size, metadata evidence, parent placement, relation, external scope and temporary-resolution needs. The application assigns source-qualified resource, version, representation and placement identifiers. A browser session is never identity.

Raise `AccessRequired` for user/session recovery, `BudgetExceeded` for an exhausted bound and `CaptureError` for a bounded method failure. Do not use authentication failure as evidence of removal. Adapter errors must not include credentials or transient signed URLs. `RuntimeActionRequired` is the host bridge's durable wait state.

Filesystem uses regular-file metadata and lexical directory checkpoints. It skips symlinks, special files and the capture project's own output tree. Directory memory is bounded to a 257-entry frontier; continuation re-enumerates names and can be expensive for very large directories. A changed directory stamp restarts that directory's enumeration. File metadata is checked before and after copying.

Web starts with bounded HTTP metadata/HTML inspection. It parses nested links, frames, media, alternate links, CSS backgrounds and responsive image references. Strong-ETag equality can reuse a navigation map while still checking children. Dynamic/authenticated pages use a browser provider when available. External references remain candidates until admitted into explicit scope. Known inaccessible assets remain in inventory with gaps. Temporary access URLs are held in memory and re-resolved through a stable parent after restart.

## Browser contract

`runtimes/contract.py` defines `capabilities`, `inspect`, and `capture`. Capabilities must describe the actual provider. `WebResponse` includes body, final location, headers, completeness, limitations, measured actions and explicit `http`/`browser` method provenance. Rendered HTML is a snapshot representation; do not label it an original HTTP response.

The optional Playwright provider accepts a supplied context or creates an ephemeral context with Chromium, Firefox or WebKit. Discovery expands supported collapsed controls, loads more content and scrolls within action/time limits; it observes open shadow roots. It reports blocked requests and incomplete stabilization. This is bounded best-effort discovery, not proof that every possible interaction has been enumerated. Browser downloads may buffer bytes before the provider can reject an oversized response. Review exposes these limits.

## Conversational host integration

1. The host uses `registry`, offers existing source selection/addition/disabling/cancellation, and registers only user-supplied locations and scope.
2. It calls `prepare_refresh` and shows the returned `review_url`. Only the user submits the local panel. The host never calls the trusted Python `decide` function or clicks approval on the user's behalf.
3. `refresh` executes a bounded number of batches. `RUNNING` means continue the same review; `NEEDS_USER` means restore access or complete a pending host action. A budget increase needs `prepare_discovery_resume` and another user review. `close_run` preserves an incomplete snapshot.
4. `index` exposes resource selection IDs, coverage, methods, limitations and estimates. The user can finish here.
5. `prepare_capture` reviews selected IDs, derivative operations and processing bounds. `capture` runs only after the panel decision. A renewed budget uses `prepare_capture_resume`; cancellation preserves completed outputs.
6. `report` exposes comparisons and quality outcomes. `prepare_compendium`/`build_compendium` combine reviewed text inputs. `export_pack` creates a local allowlist export only when requested.

If the host offers a browser, it calls `configure_host_browser` with actual `inspect`, `snapshot`, and `download` booleans. The engine emits pending `runtime_actions`. The host performs each action using its own authorised browser tools, respecting the returned scope, byte/action limits and destination. It returns results with `submit_runtime_result` and resumes the same run/job.

Inspection results contain `html`, optional stable `location`, `complete`, measured `actions` and `limitations`. Capture results require a file at the action's designated staging path plus `media_type` and limitations. Authentication remains in the host; never return cookies, passwords or signed access URLs. The bridge checks scope/size, retains per-action checkpoints and cannot grant approval. It trusts the host's account of observations; it cannot independently prove browser coverage.

The stdio service provides the MCP tools surface: initialization/discovery, ping, tool listing and tool calls. It is a local compatibility implementation, not a claim of full MCP conformance or support for every optional protocol feature. The included subprocess test checks initialization, notifications, tool listing/calls, JSON-only stdout and rejection of an approval tool. A specific desktop host installation still needs an integration check.

## Persistence contracts

SQLite schema version 1 stores JSON records by `(kind, id)`, with separate append-only events. Immutable kinds are scopes, snapshots, versions, representations, placements, artefacts, verification, provenance, comparisons and manifests. Mutable kinds hold registry availability and execution checkpoints. Unknown schema versions are rejected. Source removal never cascades into history deletion.

Captured content is keyed by SHA-256. Capture versions bind a resource to actual bytes and representation role. Derivatives bind exact input artefact/version/hash triples; multi-input compendia retain every triple. Source-qualified resource identities can remain distinct while sharing a single content object. No semantic supersession or relevance field is inferred.

`schemas/tool-definitions.json` is a generated snapshot of the live tool definitions. `schemas/contracts.schema.json` documents Scope, Budget, stored-record envelopes and Source Pack structure; application code enforces operational invariants. These schemas are not migrations and do not imply that untrusted JSON can directly grant approval.

## Future extension

`ExternalDiscoveryProvider.candidates(approved_search_brief)` is a Protocol boundary only. No implementation, search UI, automatic background invocation or routing is included. Future adapters must retain the same scope, review, identity, verification and provenance requirements.
