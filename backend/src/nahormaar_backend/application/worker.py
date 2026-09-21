# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Keep synchronous player transactions on one dedicated thread."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ..domain.models import PlayerSnapshot
from .player import Player
from .storage import PlayerStore


class PlayerWorker:
    """Create, use and close the synchronous player on one dedicated thread."""

    def __init__(self, store_factory: Callable[[], PlayerStore]) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="player")
        self._store_factory = store_factory
        self._store: PlayerStore | None = None
        self._player: Player | None = None
        self._closed = False

    async def open(self) -> PlayerSnapshot:
        if self._closed:
            raise RuntimeError("Player worker is closed.")
        try:
            return await asyncio.get_running_loop().run_in_executor(
                self._executor, self._open
            )
        except BaseException:
            await self.close()
            raise

    def _open(self) -> PlayerSnapshot:
        self._store = self._store_factory()
        self._player = Player(self._store)
        self._player.recover_requests()
        self._player.publish(self._player.revisions.revision, True)
        return self._player.snapshot

    async def call[T](self, operation: Callable[[Player], T]) -> T:
        if self._closed:
            raise RuntimeError("Player worker is closed.")
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._call, operation
        )

    def _call[T](self, operation: Callable[[Player], T]) -> T:
        if self._player is None:
            raise RuntimeError("Player worker is not open.")
        return operation(self._player)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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
