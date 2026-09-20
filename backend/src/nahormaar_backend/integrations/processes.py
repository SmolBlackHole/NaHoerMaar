# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded, cancellable child processes with process-tree cleanup."""

from __future__ import annotations

import asyncio
import math
import os
import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

import psutil

_CLEANUP_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessTimeoutError(TimeoutError):
    """A child process exceeded its total runtime limit."""


class ProcessOutputLimitError(RuntimeError):
    """A child process exceeded the configured stdout or stderr limit."""


class ProcessCleanupError(RuntimeError):
    """A child process or one of its descendants could not be reaped."""


class _ProcessTree:
    """Remember descendants while the root still owns them."""

    def __init__(self, pid: int) -> None:
        self._root: psutil.Process | None
        try:
            self._root = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self._root = None
        self._descendants: dict[int, psutil.Process] = {}
        self._access_denied = False

    def refresh(self) -> None:
        if self._root is None:
            return
        try:
            descendants = self._root.children(recursive=True)
        except psutil.NoSuchProcess:
            return
        except psutil.AccessDenied:
            self._access_denied = True
            return
        for process in descendants:
            self._descendants[process.pid] = process

    def terminate(self) -> None:
        self.refresh()
        processes = (
            *((self._root,) if self._root is not None else ()),
            *self._descendants.values(),
        )
        for process in processes:
            try:
                if process.is_running():
                    process.terminate()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                self._access_denied = True

    def kill(self) -> None:
        for process in self._descendants.values():
            try:
                if process.is_running():
                    process.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                self._access_denied = True

    def descendants_running(self) -> bool:
        for process in self._descendants.values():
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    return True
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                self._access_denied = True
        return False

    @property
    def access_denied(self) -> bool:
        return self._access_denied


async def _watch_descendants(tree: _ProcessTree) -> None:
    while True:
        tree.refresh()
        await asyncio.sleep(0.05)


async def _read_limited(
    stream: asyncio.StreamReader,
    limit: int,
    stream_name: str,
    on_line: Callable[[bytes], None] | None = None,
) -> tuple[str, bytes]:
    chunks: list[bytes] = []
    size = 0
    pending: bytes = b""
    chunk: bytes
    while chunk := await stream.read(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise ProcessOutputLimitError(f"Child process {stream_name} limit exceeded")
        chunks.append(chunk)
        if on_line is not None:
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if line.strip():
                    on_line(line.rstrip(b"\r"))
    if on_line is not None and pending.strip():
        on_line(pending.rstrip(b"\r"))
    return stream_name, b"".join(chunks)


async def _wait_for_process(
    process: asyncio.subprocess.Process,
) -> tuple[str, bytes]:
    while process.returncode is None:
        await asyncio.sleep(0.01)
    returncode = process.returncode
    return "returncode", str(returncode).encode("ascii")


async def _collect_output(
    process: asyncio.subprocess.Process,
    tree: _ProcessTree,
    limit: int,
    on_stdout_line: Callable[[bytes], None] | None = None,
) -> ProcessResult:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Child process pipes were not created")

    all_tasks: set[asyncio.Task[tuple[str, bytes]]] = {
        asyncio.create_task(
            _read_limited(process.stdout, limit, "stdout", on_stdout_line)
        ),
        asyncio.create_task(_read_limited(process.stderr, limit, "stderr")),
        asyncio.create_task(_wait_for_process(process)),
    }
    pending = set(all_tasks)
    results: dict[str, bytes] = {}
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            outcomes = await asyncio.gather(*done, return_exceptions=True)
            first_error: BaseException | None = None
            root_finished = False
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    if first_error is None:
                        first_error = outcome
                    continue
                name, value = outcome
                results[name] = value
                root_finished = root_finished or name == "returncode"
            if first_error is not None:
                raise first_error
            if root_finished:
                tree.refresh()
                if tree.descendants_running() or tree.access_denied:
                    await _terminate_process_tree(process, tree)
    finally:
        for task in all_tasks:
            task.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)

    return ProcessResult(
        returncode=int(results["returncode"]),
        stdout=results["stdout"],
        stderr=results["stderr"],
    )


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    if os.name == "nt":
        return
    kill_process_group = cast(
        Callable[[int, int], None],
        getattr(os, "killpg"),  # noqa: B009
    )
    try:
        kill_process_group(pid, sig)
    except ProcessLookupError:
        pass


async def _terminate_process_tree(
    process: asyncio.subprocess.Process, tree: _ProcessTree
) -> None:
    tree.terminate()
    _signal_process_group(process.pid, signal.SIGTERM)

    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
            await process.wait()
            while tree.descendants_running():
                await asyncio.sleep(0.01)
    except TimeoutError:
        tree.kill()
        kill_signal = cast(signal.Signals, getattr(signal, "SIGKILL", signal.SIGTERM))
        _signal_process_group(process.pid, kill_signal)
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
                await process.wait()
                while tree.descendants_running():
                    await asyncio.sleep(0.01)
        except TimeoutError:
            raise ProcessCleanupError(
                "Child process tree could not be reaped"
            ) from None
    if tree.access_denied:
        raise ProcessCleanupError("Child process tree cleanup was denied")


async def _drain_pipes(process: asyncio.subprocess.Process) -> None:
    streams = tuple(
        stream for stream in (process.stdout, process.stderr) if stream is not None
    )

    async def drain(stream: asyncio.StreamReader) -> None:
        while await stream.read(64 * 1024):
            pass

    await asyncio.gather(*(drain(stream) for stream in streams))


async def _cleanup_process(
    process: asyncio.subprocess.Process, tree: _ProcessTree
) -> None:
    drain = asyncio.create_task(_drain_pipes(process))
    termination_error: BaseException | None = None
    try:
        await _terminate_process_tree(process, tree)
    except BaseException as error:
        termination_error = error

    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
            await drain
    except TimeoutError:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        if termination_error is None:
            termination_error = ProcessCleanupError(
                "Child process pipes could not be closed"
            )
    if termination_error is not None:
        raise termination_error


async def _finish_cleanup(
    process: asyncio.subprocess.Process, tree: _ProcessTree
) -> None:
    cleanup = asyncio.create_task(_cleanup_process(process, tree))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await asyncio.shield(cleanup)
        raise


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


async def run_process(
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    max_output_bytes: int = 2 * 1024 * 1024,
    on_stdout_line: Callable[[bytes], None] | None = None,
) -> ProcessResult:
    """Run one child without a shell and reap its process tree on interruption."""
    if not args:
        raise ValueError("args must not be empty")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")

    command = tuple(os.fspath(arg) for arg in args)
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=_creation_flags(),
        )
    )

    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError as cancelled:
        try:
            process = await spawn
        except BaseException:
            raise cancelled from None
        tree = _ProcessTree(process.pid)
        await _finish_cleanup(process, tree)
        raise

    tree = _ProcessTree(process.pid)
    watcher = asyncio.create_task(_watch_descendants(tree))
    try:
        async with asyncio.timeout(timeout):
            result = await _collect_output(
                process, tree, max_output_bytes, on_stdout_line
            )
        tree.refresh()
        if tree.descendants_running() or tree.access_denied:
            await _terminate_process_tree(process, tree)
        return result
    except TimeoutError as error:
        await _finish_cleanup(process, tree)
        raise ProcessTimeoutError("Child process timed out") from error
    except asyncio.CancelledError:
        await _finish_cleanup(process, tree)
        raise
    except BaseException:
        await _finish_cleanup(process, tree)
        raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
