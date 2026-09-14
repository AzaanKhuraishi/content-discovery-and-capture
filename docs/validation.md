# Validation record

Validation was run on 14 September 2026 on macOS arm64 with Python 3.12. The deterministic test environment used the optional document dependencies. No private course corpus, Blackboard account, browser profile, cookie, signed URL or long recording was used.

## Executed checks

The complete deterministic suite, including the adversarial security regression suite, passed:

```text
Ran 52 tests
OK (skipped=4)
```

The command was:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

The 52 tests cover registry and source-selection gates, explicit scope, Web and Filesystem discovery, immutable snapshots and baselines, conservative change comparison, cross-source content deduplication, placement provenance, capture approval, finite retries, checkpoint/resume, crash reconciliation, document reading copies, DOCX/PPTX/ODT conversion, compendia, Source Pack checksums, schema validation, stdio tool compatibility, and adversarial URL, secret, Markdown, hardlink, permissions, malformed-link, output-manifest and archive cases.

The optional Playwright and Faster-Whisper tests were skipped in this run because they require a browser process and an explicitly provisioned local model. They remain manual/optional checks rather than normal CI requirements.

No live authenticated source, browser session or transcription model was used. Those checks require user-approved scopes and host capabilities.

The official plugin and MCP manifests were validated against the `agent-plugins.org` 1.0.0 schemas. The local contract schema and every generated tool input schema passed JSON Schema validation. `skill-creator` quick validation reported `Skill is valid!`.

The package built successfully as:

```text
content_discovery_and_capture-0.1.0.tar.gz
content_discovery_and_capture-0.1.0-py3-none-any.whl
```

The wheel was installed into a clean environment without the source checkout on `PYTHONPATH`; import, capabilities and the stdio `initialize`, `tools/list`, and `registry` calls succeeded.

## Checks still requiring a real deployment

The application has not been run against a live authenticated Blackboard/OneDrive collection, the original retrospective corpus, or production-length recordings. Those checks require user-approved source scopes and the target host's browser/session integration. The source tree deliberately does not create a GitHub repository, commit, push, install a global plugin, or access a private course account.
