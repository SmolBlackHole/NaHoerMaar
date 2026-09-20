# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Keep synchronous player transactions on one dedicated thread."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..domain.models import PlayerSnapshot
from ..persistence.player_store import SQLiteStore
from .player import Player


class PlayerWorker:
    """Create, use and close the synchronous player on one dedicated thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="player")
        self._store: SQLiteStore | None = None
        self._player: Player | None = None

    async def open(self, path: Path) -> PlayerSnapshot:
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._open, path
        )

    def _open(self, path: Path) -> PlayerSnapshot:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._store = SQLiteStore(path)
        self._player = Player(self._store)
        self._player.recover_requests()
        self._player.publish(self._player.revisions.revision, True)
        return self._player.snapshot

    async def call[T](self, operation: Callable[[Player], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._call, operation
        )

    def _call[T](self, operation: Callable[[Player], T]) -> T:
        if self._player is None:
            raise RuntimeError("Player worker is not open.")
        return operation(self._player)

    async def close(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self._close
            )
        finally:
            await asyncio.to_thread(self._executor.shutdown, wait=True)

    def _close(self) -> None:
        self._player = None
        if self._store is not None:
            self._store.close()
            self._store = None
