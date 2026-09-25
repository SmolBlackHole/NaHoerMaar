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
import tempfile
from collections.abc import Sequence
from pathlib import Path

from repository_checks import run_repository_checks

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
CI_IMAGE = "nahormaar-ci:local"


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _require_node() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js 24 or newer with npm is required.")
    completed = subprocess.run(  # noqa: S603 - resolved Node executable, fixed arguments
        (node, "--version"), check=True, capture_output=True, text=True
    )
    major = int(completed.stdout.strip().removeprefix("v").partition(".")[0])
    if major < 24:
        raise SystemExit("Node.js 24 or newer with npm is required.")


def _npm() -> str:
    executable = "npm.cmd" if os.name == "nt" else "npm"
    resolved = shutil.which(executable)
    if resolved is None:
        raise SystemExit("Node.js 24 or newer with npm is required.")
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
    docker = None
    if "NAHORMAAR_POSTGRES_TEST_URL" not in os.environ:
        docker = shutil.which("docker")
        if docker is None:
            raise SystemExit("Docker is required for the PostgreSQL test database.")
    try:
        if docker is not None:
            _run(
                (
                    docker,
                    "compose",
                    "--profile",
                    "test",
                    "up",
                    "-d",
                    "--wait",
                    "database-test",
                )
            )
        with tempfile.TemporaryDirectory(prefix="nahoermaar-pytest-") as pytest_temp:
            _run(
                (
                    str(python),
                    "-m",
                    "pytest",
                    f"--basetemp={pytest_temp}",
                    "-p",
                    "no:cacheprovider",
                )
            )
    finally:
        if docker is not None:
            _run(
                (
                    docker,
                    "compose",
                    "--profile",
                    "test",
                    "rm",
                    "--force",
                    "--stop",
                    "database-test",
                )
            )
    _run((_npm(), "run", "check"))


def check_container() -> None:
    """Run the complete gate in a disposable Linux container."""
    run_repository_checks(ROOT)
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("Docker is required for check-container.")
    _run((docker, "info", "--format", "Docker {{.ServerVersion}}"))
    try:
        _run(
            (
                docker,
                "compose",
                "--profile",
                "test",
                "up",
                "-d",
                "--wait",
                "database-test",
            )
        )
        _run(
            (
                docker,
                "build",
                "--file",
                "docker/Dockerfile.ci",
                "--tag",
                CI_IMAGE,
                ".",
            )
        )
        _run(
            (
                docker,
                "run",
                "--init",
                "--rm",
                "--network",
                "nahormaar_default",
                "--env",
                "NAHORMAAR_POSTGRES_TEST_URL=postgresql+psycopg://nahormaar:nahormaar-test-only@database-test:5432/nahormaar_test",
                CI_IMAGE,
            )
        )
    finally:
        _run(
            (
                docker,
                "compose",
                "--profile",
                "test",
                "rm",
                "--force",
                "--stop",
                "database-test",
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = {
        "setup": setup,
        "check": check,
        "check-container": check_container,
    }
    parser.add_argument("command", choices=commands)
    command = parser.parse_args().command
    commands[command]()


if __name__ == "__main__":
    main()
