# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Set up and verify the shared Python and Node.js workspace."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from repository_checks import run_repository_checks

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _require_node() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js 22 or newer with npm is required.")
    completed = subprocess.run(  # noqa: S603 - resolved Node executable, fixed arguments
        (node, "--version"), check=True, capture_output=True, text=True
    )
    major = int(completed.stdout.strip().removeprefix("v").partition(".")[0])
    if major < 22:
        raise SystemExit("Node.js 22 or newer with npm is required.")


def _npm() -> str:
    executable = "npm.cmd" if os.name == "nt" else "npm"
    resolved = shutil.which(executable)
    if resolved is None:
        raise SystemExit("Node.js 22 or newer with npm is required.")
    return resolved


def _run(command: Sequence[str]) -> None:
    print(f"> {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)  # noqa: S603 - fixed project tooling


def setup() -> None:
    if sys.version_info < (3, 12):  # noqa: UP036 - bootstrap runs before package installation
        raise SystemExit("Python 3.12 or newer is required.")
    _require_node()
    npm = _npm()
    if not _venv_python().is_file():
        _run((sys.executable, "-m", "venv", str(VENV)))
    _run((str(_venv_python()), "-m", "pip", "install", "-e", ".[dev]"))
    _run((str(_venv_python()), "-m", "nahormaar_backend.config"))
    _run((npm, "ci"))


def check() -> None:
    run_repository_checks(ROOT)
    python = _venv_python()
    if not python.is_file():
        raise SystemExit("Run python scripts/dev.py setup first.")
    _require_node()
    _run((str(python), "-m", "ruff", "check", "backend/src", "backend/tests"))
    _run(
        (str(python), "-m", "ruff", "format", "--check", "backend/src", "backend/tests")
    )
    _run((str(python), "-m", "mypy"))
    _run((str(python), "-m", "pyright"))
    _run((str(python), "-m", "pytest"))
    _run((_npm(), "run", "check"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "check"))
    command = parser.parse_args().command
    setup() if command == "setup" else check()


if __name__ == "__main__":
    main()
