# Validation record

Validation was run on 13 September 2026 on macOS arm64 with Python 3.12.14. The test environment used the optional document, browser and transcription dependencies, a Playwright Chromium headless shell installed under `work/browsers`, and an explicitly provisioned local Faster-Whisper tiny model under `work/models/tiny`. No private course corpus, Blackboard account, browser profile, cookie, signed URL or long recording was used.

## Executed checks

The complete suite passed with browser and transcription checks enabled:

```text
Ran 42 tests in 13.558s
OK
```

The command was:

```sh
CDC_TEST_BROWSER=1 \
CDC_TEST_AUDIO=/absolute/path/to/jfk.flac \
CDC_TRANSCRIPTION_MODEL=/absolute/path/to/local/model \
CDC_TEST_EXPECT_TEXT='ask not what your country can do for you' \
PLAYWRIGHT_BROWSERS_PATH=/absolute/path/to/work/browsers \
PYTHONPATH=src python -m unittest discover -s tests -v
```

The 42 tests cover registry and source-selection gates, explicit scope, Web and Filesystem discovery, nested/lazy browser discovery, dynamic and authenticated-runtime handoff, immutable snapshots and baselines, conservative change comparison, cross-source content deduplication, placement provenance, capture approval, finite retries, checkpoint/resume, crash reconciliation, document reading copies, PDF/DOCX/PPTX/ODT conversion, local transcription lineage, compendia, Source Pack checksums, schema validation, and stdio tool compatibility.

The real Playwright test launched Chromium successfully after the session was switched to full access and passed its nested/lazy fixture. Before that change, the same browser process failed at macOS startup with `bootstrap_check_in ... MachPortRendezvousServer ... Permission denied (1100)`; this was a host sandbox restriction rather than an application failure.

The transcription smoke test passed against the public `openai/whisper` `tests/jfk.flac` fixture. It verified nonempty timed segments, source SHA-256 linkage, SRT and JSON output, and the expected phrase. The four-placement acceptance check verified that shared audio bytes can deduplicate while four placements retain separate version-bound transcript artefacts. It does not establish production accuracy for long recordings.

The official plugin and MCP manifests were validated against the `agent-plugins.org` 1.0.0 schemas. The local contract schema and every generated tool input schema passed JSON Schema validation. `skill-creator` quick validation reported `Skill is valid!`.

The package built successfully as:

```text
content_discovery_and_capture-0.1.0.tar.gz
content_discovery_and_capture-0.1.0-py3-none-any.whl
```

The wheel was installed into a clean environment without the source checkout on `PYTHONPATH`; import, capabilities and the stdio `initialize`, `tools/list`, and `registry` calls succeeded.

## Checks still requiring a real deployment

The application has not been run against a live authenticated Blackboard/OneDrive collection, the original retrospective corpus, or production-length recordings. Those checks require user-approved source scopes and the target host's browser/session integration. The source tree deliberately does not create a GitHub repository, commit, push, install a global plugin, or access a private course account.
