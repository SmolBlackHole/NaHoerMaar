# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded, cancellable processes used by media discovery."""

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from ..application.audio import TrackError
from ..cache import CatalogBusy
from .processes import (
    ProcessOutputLimitError,
    ProcessResult,
    ProcessTimeoutError,
    run_process,
)

DISCOVERY_CONCURRENCY = 2
DISCOVERY_PENDING_LIMIT = 8


class DiscoveryExtractor:
    def __init__(self, node_path: Path) -> None:
        self._node_path = node_path
        self._slots = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
        self._tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    async def run(
        self,
        source: str,
        options: tuple[str, ...],
        *,
        timeout: float = 30,
        on_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        args = (
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-plugin-dirs",
            "--no-remote-components",
            "--no-js-runtimes",
            "--js-runtimes",
            f"node:{self._node_path}",
            "--extractor-retries",
            "0",
            "--retries",
            "0",
            "--color",
            "never",
            "--simulate",
            *options,
            "--",
            source,
        )
        return await self.execute(args, timeout=timeout, on_line=on_line)

    async def execute(
        self,
        args: tuple[str, ...],
        *,
        timeout: float = 30,
        on_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        if self._closed or len(self._tasks) >= DISCOVERY_PENDING_LIMIT:
            raise CatalogBusy("Music discovery is busy. Try again shortly.")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Discovery requires an asyncio task.")
        self._tasks.add(task)
        try:
            async with asyncio.timeout(timeout + 5), self._slots:
                return await run_process(args, timeout=timeout, on_stdout_line=on_line)
        except (TimeoutError, ProcessTimeoutError) as exc:
            raise TrackError("YouTube took too long. Try again.") from exc
        except ProcessOutputLimitError as exc:
            raise TrackError("YouTube returned too much metadata.") from exc
        except OSError as exc:
            raise TrackError("YouTube discovery could not be started.") from exc
        finally:
            self._tasks.discard(task)

    def stop_accepting(self) -> None:
        self._closed = True

    async def close(self) -> None:
        self.stop_accepting()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
