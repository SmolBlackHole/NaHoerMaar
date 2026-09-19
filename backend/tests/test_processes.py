# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import math
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from nahormaar_backend.processes import (
    ProcessCleanupError,
    ProcessOutputLimitError,
    ProcessTimeoutError,
    run_process,
)

_SPAWN_DESCENDANT = """
import pathlib
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pid_file = pathlib.Path(sys.argv[1])
pending_file = pid_file.with_suffix(".pending")
pending_file.write_text(str(child.pid), encoding="ascii")
pending_file.replace(pid_file)
time.sleep(60)
"""


def test_run_process_returns_bounded_output() -> None:
    result = asyncio.run(
        run_process(
            [sys.executable, "-c", "import sys; print('ok'); sys.stderr.write('e')"],
            timeout=2,
        )
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"ok"
    assert result.stderr == b"e"

    with pytest.raises(ProcessOutputLimitError, match="stdout"):
        asyncio.run(
            run_process(
                [sys.executable, "-c", "print('x' * 1000)"],
                timeout=2,
                max_output_bytes=20,
            )
        )


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.inf, math.nan])
def test_timeout_must_be_positive_and_finite(timeout: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        asyncio.run(run_process([sys.executable], timeout=timeout))


def test_windows_children_are_hidden_and_isolated() -> None:
    import nahormaar_backend.processes as process_module

    flags = process_module._creation_flags()  # pyright: ignore[reportPrivateUsage]
    if os.name == "nt":
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
        assert flags & subprocess.CREATE_NO_WINDOW
    else:
        assert flags == 0


def test_output_overflow_reaps_the_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "overflow.pid"
    script = """
import os
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
while True:
    sys.stdout.write("x" * 65536)
    sys.stdout.flush()
"""
    with pytest.raises(ProcessOutputLimitError, match="stdout"):
        asyncio.run(
            run_process(
                [sys.executable, "-c", script, pid_file],
                timeout=2,
                max_output_bytes=1024,
            )
        )

    process_pid = int(pid_file.read_text(encoding="ascii"))
    assert not psutil.pid_exists(process_pid)


def test_timeout_terminates_descendants(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "timeout-child.pid"

    with pytest.raises(ProcessTimeoutError):
        asyncio.run(
            run_process(
                [sys.executable, "-c", _SPAWN_DESCENDANT, child_pid_file],
                timeout=0.4,
            )
        )

    child_pid = int(child_pid_file.read_text(encoding="ascii"))
    assert not psutil.pid_exists(child_pid)


def test_successful_parent_does_not_leave_descendant(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "successful-child.pid"
    script = _SPAWN_DESCENDANT.replace("time.sleep(60)\n", "time.sleep(0.2)\n", 1)
    result = asyncio.run(
        run_process(
            [sys.executable, "-c", script, child_pid_file],
            timeout=2,
        )
    )

    child_pid = int(child_pid_file.read_text(encoding="ascii"))
    assert result.returncode == 0
    assert not psutil.pid_exists(child_pid)


def test_cancellation_terminates_descendants(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "cancelled-child.pid"

    async def cancel_process() -> int:
        task = asyncio.create_task(
            run_process(
                [sys.executable, "-c", _SPAWN_DESCENDANT, child_pid_file],
                timeout=10,
            )
        )
        async with asyncio.timeout(2):
            while not child_pid_file.exists():
                await asyncio.sleep(0.01)
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return child_pid

    child_pid = asyncio.run(cancel_process())
    assert not psutil.pid_exists(child_pid)


def test_cleanup_failure_is_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nahormaar_backend.processes as process_module

    class FakeProcess:
        pid = 999_999_999
        returncode = 0

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:
            pass

    class StuckTree:
        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def descendants_running(self) -> bool:
            return True

    monkeypatch.setattr(process_module, "_CLEANUP_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ProcessCleanupError, match="could not be reaped"):
        asyncio.run(
            process_module._terminate_process_tree(  # pyright: ignore[reportPrivateUsage]
                FakeProcess(),  # type: ignore[arg-type]
                StuckTree(),  # type: ignore[arg-type]
            )
        )
