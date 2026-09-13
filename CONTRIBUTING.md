# Contributing

Use Python 3.12 or newer. Install the package in a local environment and run `PYTHONPATH=src python -m unittest discover -s tests -v`.

Keep the production source registry limited to Web and Filesystem for V1. Add capture methods and runtime providers behind their contracts. Source adapters must never approve capture, widen scope, silently drop versions or treat authentication failures as deletion.

Use synthetic, redistributable fixtures. Do not commit captured projects, browser profiles, cookies, signed URLs, private course material, model weights or local test environments.

For changes to persistence, provide explicit schema migration behaviour and tests that preserve old records. For capture and discovery changes, test interrupted execution and denied approvals in addition to successful output. Run the optional browser/transcription tests when modifying those providers in an environment that supports them.

Build distributions with `python -m build`. Install the resulting wheel into a clean environment and test imports and the stdio tools surface outside the source checkout. Keep current known limitations and executed validation evidence accurate.
