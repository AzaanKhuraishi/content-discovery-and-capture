# Content Discovery and Capture

These instructions apply to this repository only. It is a standalone Content Discovery and Capture application. Never add its files to, or change, the Academic Assignment Assistant or any other repository.

## First-time setup

When a user says “Set up Content Discovery and Capture”, or this is the first task in a fresh session, take care of setup before asking what they want to discover:

1. Read the user-facing journey in `README.md`.
2. Run `scripts/bootstrap.py` from this repository. Keep its environment, dependency installation, service setup and validation in the background. If the shell does not have Python 3.12 or newer, use the Codex workspace dependency runtime to locate a supported Python interpreter and run the script with it. Do not ask the user to install Python, create a virtual environment, run commands, or configure MCP.
3. The bootstrap creates an isolated `.codex/venv`, installs the application with its lightweight document and image capabilities, creates the private `.codex/data` capture project, and checks that the service can start. It must not install browser binaries, download transcription models, or access a live source during setup.
4. Use the project-scoped `.codex/config.toml` MCP entry and `scripts/codex-mcp`; do not add a global MCP entry. If the server is not yet visible after setup, restart the Codex session yourself and continue.
5. Once setup succeeds, say that Content Discovery and Capture is ready and ask exactly: “What content would you like me to discover?” Do not expose Python, environment, MCP, service, dependency, or architecture details unless the user asks for them.

If setup fails, keep the explanation user-facing, state what access is needed, and do not leave the user with terminal instructions. Do not claim readiness until the bootstrap check succeeds.

## Normal use

Use the Content Discovery and Capture MCP tools and the workflow in `integrations/conversational/skills/content-discovery-capture/SKILL.md`. Keep source registration, scope, discovery budgets, capture choices, provenance and the local user review gate intact. Never approve a review on the user's behalf. Ask the user to authenticate through the source's normal browser flow when required.

Do not run browser, transcription, model-download or live-source integration checks as part of first-time setup. Run those only when the user explicitly asks for them and the required capability is available.

When the user gives a folder or website, explain the choices in ordinary language and keep implementation details out of the conversation. Content Discovery and Capture has exactly two V1 source adapters: Web and Filesystem. It does not perform open-web research, automatic scheduling or Academic Assignment Assistant integration.

## Changes and validation

Keep changes scoped to this repository. Before presenting a change for review, run the relevant deterministic tests and package build. Do not commit private capture data, browser profiles, cookies, signed URLs, model weights or local environments.
