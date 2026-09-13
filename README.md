# Content Discovery and Capture

Discover a collection, understand what is available, and choose what to capture. Keep original bytes, version history, reading copies and provenance distinct.

This is the local V1 implementation. It has exactly two source adapters: **Web** and **Filesystem**. Its application engine runs independently of an agent host. A conversational plugin provides the interaction layer; the host contributes its available browsing and reasoning capabilities.

## Using the application

After setup in a compatible local host, ordinary requests look like:

> Index this folder without copying or changing anything.

> Check for new material. It is also being provided in a different shared folder now.

> Capture the selected lessons and attachments, but skip video.

> Make reading copies, then combine these six into a compendium.

> Create a compact Source Pack containing these captured versions.

For refresh, the application first presents the source registry. Choose existing sources, add a bounded new location, disable a source, or cancel. It then shows the discovery budget in a local review panel. Capture has a separate review with explicit selections, derivations and budgets. The conversational agent cannot grant these approvals through its tool API.

Index-only is a completed workflow. Access failures and incomplete scans never replace the last successful baseline. Capturing a new version never makes an earlier version or its derivatives stale or superseded. Removing a source from future checks retains the captured history.

## Included

- Multi-source registry, scope revisions, independent availability and discovery history.
- Incremental filesystem traversal, bounded HTTP inspection, strong-ETag navigation reuse, external-reference inventory, frames/media/alternate representations and protected-resource resolution.
- An optional Playwright provider and a typed host-browser action bridge for runtimes such as Codex. Browser capabilities are explicitly advertised; sessions are not stored in source identity.
- Immutable discovery snapshots, evidence-qualified delta reports, source-selection and capture gates.
- Checkpointed capture, version-bound originals/derivatives, finite retry accounting and reviewed budget renewal.
- Document reading copies for HTML, text, DOCX, PPTX, ODT and PDF where the relevant optional converters are installed.
- Local transcription through an explicitly provisioned Faster-Whisper model; Markdown, SRT and first-pass JSON with source hash, timings and review flags.
- Hash-based storage deduplication, separate placement provenance, filename conflicts, bounded textual near-duplicate candidates and evidence-based classification annotations.
- Multi-input compendia, standalone capture manifests, and allowlist-built Source Packs with verified checksums.

No cloud API source adapters, open-web research, automatic scheduler, or Academic Assignment Assistant integration are included. The External Discovery Provider is a protocol boundary only.

## Setup for developers and host administrators

The engine requires Python 3.12 or newer. Use a local environment outside any source being captured. The current execution lock implementation targets macOS and Linux.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install .
```

Install optional capabilities only when needed:

```sh
.venv/bin/python -m pip install '.[documents,images,browser,transcription]'
.venv/bin/python -m playwright install chromium
```

Configure the host to run the absolute installed `content-capture` executable:

```sh
content-capture --project /path/to/private-capture-project serve
```

The stdio service implements the MCP tools surface and starts a loopback-only review panel. Only JSON-RPC responses appear on stdout. The host presents returned review links to the user; the user decides in the panel. The server must remain running for those links to work. A separately started service can reopen the same durable pending reviews at a new local address.

`integrations/conversational/` is the portable plugin wrapper. It assumes the installed `content-capture` executable is available to its host; set an absolute executable path in a local copy if needed. `${PLUGIN_DATA}/collection` is the default private project location. Installing or publishing the plugin globally is a separate setup action; this source tree does not modify host configuration.

The application does not assume an MCP server can directly access other browser tools. A host may use `configure_host_browser`, read `runtime_actions`, perform the requested scoped action with its own authorised tools, and return evidence through `submit_runtime_result`. See [runtime contracts](docs/adapter-contracts.md).

To launch an independent ephemeral browser through Playwright, use `--browser chromium`, `--browser firefox`, or `--browser webkit` before `serve`. The selected engine must already be installed. Authenticate in the opened browser when prompted. The app never saves browser storage state or cookies.

To enable transcription, provision a local CTranslate2 Whisper model and set `CDC_TRANSCRIPTION_MODEL` to its directory in the service environment. The application uses local-files-only loading and does not automatically download models. With no model, transcription is UNAVAILABLE and originals can still be captured. The tiny model used for a development smoke test is not a production quality recommendation.

## Developer diagnostics

```sh
content-capture --project /path/to/project capabilities
content-capture --project /path/to/project call registry
content-capture --project /path/to/project call resume_status
```

There is no CLI or model tool that accepts `approved: true`. For manual developer use, run `content-capture --project /path/to/project review` and open `/review/<review-id>` on the address it prints. The trusted panel is the approval authority. This is a workflow boundary, not an operating-system sandbox against a malicious process that already has the user's privileges.

## Validation

```sh
PYTHONPATH=src python3.12 -m unittest discover -s tests -v
```

Set `CDC_TEST_BROWSER=1` to enable the optional headless browser test in an environment that permits browser processes. Set `CDC_TEST_AUDIO` to a short synthetic/local audio fixture and `CDC_TRANSCRIPTION_MODEL` to an installed model to enable the real transcription test. Tests never fetch models or use private course content automatically.

See [validation results](docs/validation.md), [acceptance evidence](docs/acceptance-matrix.md), [architecture](docs/architecture.md) and [security boundaries](SECURITY.md). The release has explicit integration limits; these documents distinguish executed tests from unverified live-source scenarios.

## Storage and portability

A private capture project holds SQLite metadata, immutable content objects, bounded staging files, versioned reports and optional exported ZIPs. Capture projects and secrets must not be committed to the application repository. Metadata schema version 1 is supported; unknown versions are rejected rather than rewritten.

Source Pack members are selected from an explicit manifest, not a recursive project scan. Checksums cover every other member; the archive hash is returned separately. Original source links can remain in captured material. A pack is a portable corpus, not a guaranteed fully offline replica of an interactive website. Credential-like text prevents unsafe export; keep the private original and select a sanitised reading derivative.

The software is MIT licensed. Captured material retains its own licensing and provenance.
