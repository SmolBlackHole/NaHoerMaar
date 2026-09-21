# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path
from threading import get_ident

import pytest

from nahormaar_backend.application.storage import PlayerStore, StorageError
from nahormaar_backend.application.worker import PlayerWorker
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.models import PlayerSnapshot, QueueEntry
from nahormaar_backend.persistence.player_store import SQLiteStore


def test_factory_transactions_and_close_share_one_worker_thread(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    class ObservedStore(SQLiteStore):
        def load(self) -> PlayerSnapshot:
            calls.append(("load", get_ident()))
            return super().load()

        def checkpoint(self) -> PlaybackCheckpoint | None:
            calls.append(("checkpoint", get_ident()))
            return super().checkpoint()

        def close(self) -> None:
            calls.append(("close", get_ident()))
            super().close()

    def factory() -> PlayerStore:
        calls.append(("factory", get_ident()))
        return ObservedStore(tmp_path / "worker.sqlite3")

    async def scenario() -> None:
        worker = PlayerWorker(factory)
        try:
            assert await worker.open() == PlayerSnapshot()
            entry = QueueEntry("https://youtu.be/example")
            assert (
                await worker.call(lambda player: player.enqueue(entry))
            ).upcoming == (entry,)
        finally:
            await worker.close()
        await worker.close()
        with pytest.raises(RuntimeError, match="closed"):
            await worker.call(lambda player: player.snapshot)

    asyncio.run(scenario())
    assert {name for name, _ in calls} == {"factory", "load", "checkpoint", "close"}
    assert len({thread for _, thread in calls}) == 1
    assert calls[0][1] != get_ident()
    assert [name for name, _ in calls].count("close") == 1


def test_factory_failure_closes_worker() -> None:
    factory_threads: list[int] = []

    def factory() -> PlayerStore:
        factory_threads.append(get_ident())
        raise StorageError("Factory failed")

    async def scenario() -> None:
        worker = PlayerWorker(factory)
        with pytest.raises(StorageError, match="Factory failed"):
            await worker.open()
        with pytest.raises(RuntimeError, match="closed"):
            await worker.open()
        await worker.close()

    asyncio.run(scenario())
    assert len(factory_threads) == 1
    assert factory_threads[0] != get_ident()


def test_player_load_failure_closes_allocated_store_on_worker_thread(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, int]] = []

    class BrokenStore(SQLiteStore):
        loads = 0

        def load(self) -> PlayerSnapshot:
            calls.append(("load", get_ident()))
            self.loads += 1
            if self.loads == 2:
                raise StorageError("Player load failed")
            return super().load()

        def close(self) -> None:
            calls.append(("close", get_ident()))
            super().close()

    async def scenario() -> None:
        worker = PlayerWorker(lambda: BrokenStore(tmp_path / "worker.sqlite3"))
        with pytest.raises(StorageError, match="Player load failed"):
            await worker.open()
        await worker.close()

    asyncio.run(scenario())
    assert [name for name, _ in calls] == ["load", "load", "close"]
    assert len({thread for _, thread in calls}) == 1
    assert calls[0][1] != get_ident()
