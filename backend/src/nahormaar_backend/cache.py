# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded metadata snapshots with shared background refreshes."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import cast
from uuid import uuid4

SNAPSHOT_TTL = 1800.0


class CatalogBusy(Exception):
    """Discovery is at capacity; playback has its own resolver."""


@dataclass(frozen=True, slots=True)
class Snapshot[T]:
    id: str
    value: T
    checked_at: float


class SnapshotCache[T]:
    def __init__(
        self, limit: int, refresh_after: float, pending_limit: int = 8
    ) -> None:
        self.limit = limit
        self.refresh_after = refresh_after
        self.pending_limit = pending_limit
        self.values: OrderedDict[str, Snapshot[T]] = OrderedDict()
        self.versions: OrderedDict[str, tuple[str, Snapshot[T], float]] = OrderedDict()
        self.pending: dict[str, asyncio.Task[Snapshot[T]]] = {}
        self.errors: dict[str, str] = {}
        self.attempts: dict[str, float] = {}
        self.closed = False

    def peek(self, key: str) -> Snapshot[T] | None:
        value = self.values.get(key)
        if value is not None:
            self.values.move_to_end(key)
        return value

    def version(self, key: str, identifier: str) -> Snapshot[T]:
        item = self.versions.get(identifier)
        if item is None or item[0] != key or item[2] <= monotonic():
            raise KeyError(identifier)
        return item[1]

    def put(self, key: str, value: T) -> Snapshot[T]:
        previous = self.values.get(key)
        identifier = (
            previous.id if previous and previous.value == value else uuid4().hex
        )
        snapshot = Snapshot(identifier, value, monotonic())
        self.values[key] = snapshot
        self.values.move_to_end(key)
        self.versions[identifier] = (key, snapshot, monotonic() + SNAPSHOT_TTL)
        self.versions.move_to_end(identifier)
        self.errors.pop(key, None)
        while len(self.values) > self.limit:
            removed, _ = self.values.popitem(last=False)
            self.errors.pop(removed, None)
            self.attempts.pop(removed, None)
        for old, (_, _, expires) in tuple(self.versions.items()):
            if expires <= monotonic():
                del self.versions[old]
        while len(self.versions) > self.limit * 3:
            self.versions.popitem(last=False)
        return snapshot

    def request(
        self, key: str, load: Callable[[], Awaitable[T]], *, force: bool = False
    ) -> asyncio.Task[Snapshot[T]] | None:
        if self.closed:
            raise CatalogBusy("Music discovery is closed.")
        if key in self.pending:
            return self.pending[key]
        cached = self.peek(key)
        if cached:
            self.versions[cached.id] = (key, cached, monotonic() + SNAPSHOT_TTL)
        checked = max(cached.checked_at, self.attempts.get(key, 0)) if cached else 0
        if (
            cached
            and (not force or key in self.attempts)
            and monotonic() - checked < self.refresh_after
        ):
            return None
        if len(self.pending) >= self.pending_limit:
            if cached:
                return None
            raise CatalogBusy("Music discovery is busy. Try again shortly.")
        self.attempts[key] = monotonic()
        self.errors.pop(key, None)
        task = asyncio.create_task(self._load(key, load))
        self.pending[key] = task
        task.add_done_callback(self._consume_failure)
        return task

    async def get(self, key: str, load: Callable[[], Awaitable[T]]) -> Snapshot[T]:
        task = self.request(key, load)
        cached = self.peek(key)
        if cached is not None:
            return cached
        return await asyncio.shield(cast(asyncio.Task[Snapshot[T]], task))

    async def _load(self, key: str, load: Callable[[], Awaitable[T]]) -> Snapshot[T]:
        try:
            return self.put(key, await load())
        except Exception:
            if key in self.values:
                self.errors[key] = "Could not refresh. Showing saved results."
            raise
        finally:
            self.pending.pop(key, None)
            if key not in self.values:
                self.attempts.pop(key, None)

    @staticmethod
    def _consume_failure(task: asyncio.Task[Snapshot[T]]) -> None:
        if not task.cancelled():
            task.exception()

    async def cancel(self, key: str) -> None:
        task = self.pending.get(key)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.values.clear()
        self.versions.clear()
        self.errors.clear()
        self.attempts.clear()
