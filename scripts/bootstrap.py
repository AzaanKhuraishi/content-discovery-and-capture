#!/usr/bin/env python3
"""Prepare the private, repository-local runtime used by the Codex MCP entry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


MIN_PYTHON = (3, 12)
BOOTSTRAP_VERSION = 2
ROOT = Path(__file__).resolve().parents[1]
CODEX_DIR = ROOT / ".codex"
VENV = CODEX_DIR / "venv"
DATA = CODEX_DIR / "data"
MARKER = CODEX_DIR / "bootstrap.json"


class BootstrapError(RuntimeError):
    """A setup failure that can be shown without a traceback."""


def supported(executable: str) -> bool:
    try:
        result = subprocess.run(
            [executable, "-c", "import sys; raise SystemExit(sys.version_info < (3, 12))"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def find_supported_python() -> str | None:
    candidates = []
    configured = os.environ.get("CDC_BOOTSTRAP_PYTHON")
    if configured:
        candidates.append(configured)
    if sys.version_info >= MIN_PYTHON:
        candidates.append(sys.executable)
    for name in ("python3.13", "python3.12", "python3", "python"):
        located = shutil.which(name)
        if located:
            candidates.append(located)
    codex_runtimes = Path.home() / ".cache" / "codex-runtimes"
    if codex_runtimes.is_dir():
        candidates.extend(str(path) for path in sorted(codex_runtimes.glob("*/dependencies/python/bin/python3")))
    seen = set()
    for candidate in candidates:
        if candidate not in seen and supported(candidate):
            seen.add(candidate)
            return candidate
        seen.add(candidate)
    return None


def run(command: list[str], *, quiet: bool = False) -> None:
    try:
        subprocess.run(command, cwd=ROOT, check=True, stdout=subprocess.DEVNULL if quiet else None)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None)
        suffix = f": {detail.strip()}" if isinstance(detail, str) and detail.strip() else ""
        raise BootstrapError(f"The local setup step could not complete{suffix}.") from error


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    paths = [ROOT / "pyproject.toml", Path(__file__)]
    paths.extend(sorted((ROOT / "src").rglob("*.py")))
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def marker_matches() -> bool:
    if not MARKER.exists() or not (VENV / "bin" / "python").exists():
        return False
    try:
        record = json.loads(MARKER.read_text())
        return record.get("bootstrap_version") == BOOTSTRAP_VERSION and record.get("source_fingerprint") == source_fingerprint()
    except (OSError, ValueError, TypeError):
        return False


def create_environment(interpreter: str, quiet: bool) -> Path:
    CODEX_DIR.mkdir(exist_ok=True, mode=0o700)
    CODEX_DIR.chmod(0o700)
    if not VENV.exists():
        run([interpreter, "-m", "venv", str(VENV)], quiet=quiet)
    venv_python = VENV / "bin" / "python"
    if not venv_python.exists():
        raise BootstrapError("The private application environment could not be created.")
    if not supported(str(venv_python)):
        raise BootstrapError("The private application environment is not using a supported Python version.")
    return venv_python


def install_application(venv_python: Path, quiet: bool) -> None:
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], quiet=quiet)
    run([str(venv_python), "-m", "pip", "install", ".[documents,images]"], quiet=quiet)


def verify_runtime(venv_python: Path, quiet: bool) -> None:
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.chmod(0o700)
    run(
        [
            str(venv_python),
            "-c",
            "import content_discovery_capture; from content_discovery_capture.interfaces.cli import main; print(content_discovery_capture.__name__)",
        ],
        quiet=quiet,
    )
    run([str(VENV / "bin" / "content-capture"), "--project", str(DATA), "capabilities"], quiet=quiet)
    try:
        service = subprocess.run(
            [str(VENV / "bin" / "content-capture"), "--project", str(DATA), "serve"],
            cwd=ROOT,
            input=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25"},
                }
            )
            + "\n",
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        response = json.loads(service.stdout.splitlines()[0])
        if response.get("result", {}).get("serverInfo", {}).get("name") != "content-discovery-and-capture":
            raise ValueError("unexpected service response")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, IndexError, json.JSONDecodeError) as error:
        raise BootstrapError("The local application service could not be started.") from error


def write_marker(venv_python: Path) -> None:
    CODEX_DIR.mkdir(exist_ok=True, mode=0o700)
    CODEX_DIR.chmod(0o700)
    python_version = subprocess.check_output(
        [str(venv_python), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        text=True,
    ).strip()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=CODEX_DIR, prefix="bootstrap-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(
                {
                    "bootstrap_version": BOOTSTRAP_VERSION,
                    "source_fingerprint": source_fingerprint(),
                    "python": python_version,
                    "environment": str(venv_python),
                },
                handle,
                indent=2,
            )
            handle.write("\n")
        temporary.replace(MARKER)
        MARKER.chmod(0o600)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def bootstrap(*, quiet: bool = False, force: bool = False) -> None:
    if marker_matches() and not force:
        return
    interpreter = find_supported_python()
    if not interpreter:
        raise BootstrapError("A supported Python runtime is unavailable to the Codex workspace.")
    venv_python = create_environment(interpreter, quiet)
    install_application(venv_python, quiet)
    verify_runtime(venv_python, quiet)
    write_marker(venv_python)
    if not quiet:
        print("Content Discovery and Capture is ready.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare Content Discovery and Capture for Codex")
    parser.add_argument("--quiet", action="store_true", help="Keep setup output out of the conversation")
    parser.add_argument("--force", action="store_true", help="Reinstall the private environment")
    args = parser.parse_args(argv)
    try:
        bootstrap(quiet=args.quiet, force=args.force)
    except BootstrapError as error:
        print(f"Content Discovery and Capture setup could not finish: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
