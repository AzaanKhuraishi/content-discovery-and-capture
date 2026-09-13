# V1 acceptance evidence

The approved product specification and subsequent refresh/version delta are normative. The Effective Consultancy retrospective supplies empirical scenarios. No Blackboard-specific IDs, selectors, lesson counts, credentials or private corpus are production constants.

| Requirement | Evidence |
| --- | --- |
| Exactly Web and Filesystem, independent runtime | Adapter protocols, runtime contracts and application adapter registry; filesystem and HTTP tests |
| Registry before source I/O; add sources over time | `test_application.py` gate, multisource and registry tests; registration/review perform no source inspection |
| Mandatory separate capture review; index-only valid | Rejected unapproved operations, index-only zero capture objects, protocol rejection of approval tool |
| Scope and external candidates | Web out-of-scope links, query/traversal checks, filesystem symlink exclusion, scope-revision invalidation |
| Independent immutable discovery history | Immutable snapshot checks, source-specific baselines, incomplete scan and authentication failure tests |
| Conservative changes and rename evidence | Changed metadata/hash, rename, scope changes and ETag child checks; absence requires comparable successful coverage |
| Exact previous-version preservation | Changed-file rejection after review and multiple-version/derivative lineage tests |
| Duplicate content versus placements | Cross-source hash deduplication, filename conflict and retrospective-shaped 27 placements/25 unique binaries |
| Runtime-aware feasibility | Method availability, unavailable representations, optional converter/model checks and host capability bridge |
| Authenticated access recovery | Simulated HTTP access failure plus typed host observation/capture actions; live authentication remains unverified |
| Lazy/nested/dynamic discovery | Deterministic parser and host contract tests; actual Playwright nested/lazy fixture exists but browser launch was blocked locally |
| Bounded retries and checkpoint resume | Partial transfer, crash after artefact commit, persistent retry exhaustion and discovery-budget renewal tests |
| Preserve and verify originals | Byte hash, structural/decoder checks, login-page/PDF mismatch rejection; converter tests preserve source files |
| Reading copies and local transcripts | HTML/ODT, DOCX table ordering, PPTX speaker notes, PDF page evidence; explicit local Whisper smoke tests |
| Provenance/manifest/optional pack | Standalone manifests, exact version inputs, archive checksums and allowlist exclusions |
| Reusable refresh and resume prompt | User-triggered refresh, configurable prompt test, no scheduler or automatic capture |
| Wider-web discovery and consuming app integration excluded | Future provider Protocol only; no open-web search or Academic Assignment Assistant integration |

## Retrospective-shaped fixture

`test_acceptance.py` creates a synthetic HTTP collection with 49 lesson pages, 74 image references, 71 accessible image placements, three unavailable image resources, and 27 attachment placements representing 25 unique binaries. Discovery keeps the unavailable resources explicit and conservatively leaves the snapshot incomplete rather than advancing a successful baseline. Binary attachment GET requests begin only after capture approval.

The same fixture captures 71 image placements, produces 19 selected lesson reading copies, and combines selected readings into six version-bound compendia. Shared image bytes deduplicate without losing placement records. This tests reusable operations with historical workload shapes, not reproduction of the original course content.

An optional transcription acceptance test captures four placements of a short explicitly supplied test recording and produces four Markdown, four SRT and four JSON derivatives. It verifies separate source-version lineage despite shared audio bytes. A separate inference check verifies nonempty timed segments, source SHA-256 and an optional expected phrase. This does not validate four distinct course recordings or long-recording quality.

## Remaining integration acceptance

- Launch the optional Playwright fixture in an environment that allows browser processes.
- Install the plugin in the intended host and verify conversational source selection, user-panel review, authentication handoff and host action execution end to end.
- With user-approved source scope and selections, replay a real authenticated collection and inspect lazy navigation, embedded assets, downloads and incomplete branches.
- Evaluate long-recording transcription and review quality with the intended local model and hardware. A tiny-model smoke test establishes operation, not production transcription accuracy.

These are explicit integration checks still outstanding. Synthetic coverage is not reported as successful live-course acceptance.
