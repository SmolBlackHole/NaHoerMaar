# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded observations with shared refresh work and immutable visible versions."""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from time import monotonic
from uuid import UUID, uuid4

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Snapshot[T]:
    version: UUID
    value: T


@dataclass(frozen=True, slots=True)
class RefreshStatus:
    latest_version: UUID
    refreshing: bool
    error: str | None


@dataclass(slots=True)
class _Cached[T]:
    snapshot: Snapshot[T]
    refresh_after: float
    error: str | None = None


class SnapshotCache[K: Hashable, T]:
    """One event-loop owner; consumers keep a version rather than mutable rows.

    TTL controls revalidation, retention bounds how long old selections remain
    available. Failure retains known data and backs off. No automatic timers or
    unbounded request queue: callers trigger refresh and the owner closes tasks.
    """

    def __init__(
        self,
        *,
        ttl: float,
        capacity: int = 32,
        retention: float = 900,
        versions: int = 128,
        max_pending: int = 4,
        name: str = "catalog",
        clock: Callable[[], float] = monotonic,
        error: Callable[[T], str | None] = lambda value: None,
    ) -> None:
        if ttl <= 0 or retention < ttl or min(capacity, versions, max_pending) < 1:
            raise ValueError("Cache limits must be positive; retention must cover TTL.")
        self._ttl, self._retention = ttl, retention
        self._capacity, self._versions, self._max_pending = (
            capacity,
            versions,
            max_pending,
        )
        self._clock, self._error = clock, error
        self._name = name
        self._latest: OrderedDict[K, _Cached[T]] = OrderedDict()
        self._snapshots: OrderedDict[UUID, tuple[K, float, Snapshot[T]]] = OrderedDict()
        self._pending: dict[K, asyncio.Task[Snapshot[T]]] = {}
        self._closed = False

    def _prune(self) -> None:
        now = self._clock()
        for version, (_, expiry, _) in tuple(self._snapshots.items()):
            if expiry <= now:
                del self._snapshots[version]
        for key, cached in tuple(self._latest.items()):
            if cached.snapshot.version not in self._snapshots:
                del self._latest[key]

    async def get(
        self,
        key: K,
        load: Callable[[], Awaitable[T]],
        *,
        refresh: bool = False,
    ) -> Snapshot[T]:
        if self._closed:
            raise RuntimeError("Catalog cache is closed.")
        self._prune()
        cached = self._latest.get(key)
        if cached is not None:
            self._latest.move_to_end(key)
        pending = self._pending.get(key)
        if pending is None and (
            cached is None or refresh or self._clock() >= cached.refresh_after
        ):
            if len(self._pending) >= self._max_pending:
                if cached is None:
                    _LOGGER.warning(
                        "engine.cache.busy name=%s pending=%s limit=%s",
                        self._name,
                        len(self._pending),
                        self._max_pending,
                    )
                    raise RuntimeError("Catalog discovery is busy. Try again shortly.")
                cached.error = "Refresh is waiting for other discovery requests."
                _LOGGER.debug(
                    "engine.cache.refresh_deferred name=%s pending=%s",
                    self._name,
                    len(self._pending),
                )
            else:
                _LOGGER.info(
                    "engine.cache.refresh_started name=%s cached=%s",
                    self._name,
                    cached is not None,
                )
                pending = asyncio.create_task(self._load(key, load))
                self._pending[key] = pending
                pending.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception()
                )
        if cached is not None:
            _LOGGER.debug(
                "engine.cache.served name=%s version=%s refreshing=%s",
                self._name,
                cached.snapshot.version,
                pending is not None,
            )
            return cached.snapshot
        if pending is not None:
            _LOGGER.debug("engine.cache.waiting name=%s", self._name)
            return await asyncio.shield(pending)
        raise RuntimeError("No catalog result or pending request is available.")

    async def _load(self, key: K, load: Callable[[], Awaitable[T]]) -> Snapshot[T]:
        started = time.monotonic()
        try:
            value = await load()
            previous = self._latest.get(key)
            error = self._error(value)
            if (
                previous is not None
                and error
                and not self._error(previous.snapshot.value)
            ):
                previous.error = error
                previous.refresh_after = self._clock() + min(self._ttl, 30)
                _LOGGER.info(
                    "engine.cache.refresh_kept_previous name=%s elapsed=%.3f",
                    self._name,
                    time.monotonic() - started,
                )
                return previous.snapshot
            snapshot = (
                previous.snapshot
                if previous is not None and previous.snapshot.value == value
                else Snapshot(uuid4(), value)
            )
            self._snapshots[snapshot.version] = (
                key,
                self._clock() + self._retention,
                snapshot,
            )
            self._snapshots.move_to_end(snapshot.version)
            self._latest[key] = _Cached(snapshot, self._clock() + self._ttl, error)
            self._latest.move_to_end(key)
            while len(self._latest) > self._capacity:
                self._latest.popitem(last=False)
            while len(self._snapshots) > self._versions:
                self._snapshots.popitem(last=False)
            self._prune()
            _LOGGER.info(
                "engine.cache.refresh_completed name=%s changed=%s elapsed=%.3f",
                self._name,
                previous is None or snapshot.version != previous.snapshot.version,
                time.monotonic() - started,
            )
            return snapshot
        except Exception as error:
            if (previous := self._latest.get(key)) is not None:
                previous.error = "Could not refresh these results. The previous version is still available."
                previous.refresh_after = self._clock() + min(self._ttl, 30)
            _LOGGER.error(
                "engine.cache.refresh_failed name=%s cached=%s elapsed=%.3f",
                self._name,
                previous is not None,
                time.monotonic() - started,
                exc_info=error,
            )
            raise
        finally:
            self._pending.pop(key, None)

    def read(self, version: UUID) -> Snapshot[T]:
        self._prune()
        found = self._snapshots.get(version)
        if found is None:
            _LOGGER.info(
                "engine.cache.snapshot_expired name=%s version=%s",
                self._name,
                version,
            )
            raise ValueError("These results have expired. Open them again.")
        _LOGGER.debug(
            "engine.cache.snapshot_read name=%s version=%s", self._name, version
        )
        return found[2]

    def status(self, version: UUID) -> RefreshStatus:
        self.read(version)
        key = self._snapshots[version][0]
        latest = self._latest.get(key)
        return RefreshStatus(
            latest.snapshot.version if latest else version,
            key in self._pending,
            latest.error if latest else None,
        )

    async def close(self) -> None:
        _LOGGER.debug(
            "engine.cache.closing name=%s pending=%s cached=%s snapshots=%s",
            self._name,
            len(self._pending),
            len(self._latest),
            len(self._snapshots),
        )
        self._closed = True
        tasks = tuple(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._latest.clear()
        self._snapshots.clear()
        _LOGGER.debug("engine.cache.closed name=%s", self._name)
