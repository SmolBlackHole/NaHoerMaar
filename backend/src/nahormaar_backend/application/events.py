# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Latest-only UI snapshots and ordered, bounded committed-change subscribers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from ..domain.models import HistoryEntry

from .status import PlaybackStatus

_LOGGER = logging.getLogger(__name__)


class Snapshots[T]:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[T | None]] = set()

    def subscribe(self, initial: T) -> asyncio.Queue[T | None]:
        queue: asyncio.Queue[T | None] = asyncio.Queue(maxsize=1)
        queue.put_nowait(initial)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[T | None]) -> None:
        self._subscribers.discard(queue)

    def publish(self, snapshot: T | None) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)

    def close(self) -> None:
        self.publish(None)
        self._subscribers.clear()


@dataclass(frozen=True, slots=True)
class CommittedChange:
    before: PlaybackStatus
    after: PlaybackStatus


@dataclass(frozen=True, slots=True)
class QueueChanged(CommittedChange):
    pass


@dataclass(frozen=True, slots=True)
class PlaybackChanged(CommittedChange):
    pass


@dataclass(frozen=True, slots=True)
class RadioChanged(CommittedChange):
    pass


@dataclass(frozen=True, slots=True)
class TrackStarted(CommittedChange):
    play: HistoryEntry


type SessionEvent = QueueChanged | PlaybackChanged | RadioChanged | TrackStarted


class Subscription:
    """One ordered optional consumer; explicit latest mode is for invalidations."""

    def __init__(
        self,
        kinds: tuple[type[SessionEvent], ...],
        callback: Callable[[SessionEvent], Awaitable[None]],
        *,
        capacity: int,
        latest: bool,
        finished: Callable[[Subscription], None],
    ) -> None:
        self._kinds = kinds
        self._callback = callback
        self._queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=capacity)
        self._latest = latest
        self._closed = False
        self._finished = finished
        self._task = asyncio.create_task(self._run(), name="session-event-subscriber")
        self._task.add_done_callback(self._done)

    @property
    def closed(self) -> bool:
        return self._closed

    def offer(self, event: SessionEvent) -> None:
        if self._closed or not isinstance(event, self._kinds):
            return
        if self._queue.full():
            if not self._latest:
                _LOGGER.error(
                    "session.events.subscriber_overflow capacity=%s event=%s; "
                    "subscription detached",
                    self._queue.maxsize,
                    type(event).__name__,
                )
                self._stop()
                return
            self._queue.get_nowait()
            self._queue.task_done()
        self._queue.put_nowait(event)

    async def _run(self) -> None:
        try:
            while not self._closed:
                event = await self._queue.get()
                try:
                    await self._callback(event)
                except Exception:
                    _LOGGER.exception(
                        "session.events.subscriber_failed event=%s revision=%s",
                        type(event).__name__,
                        event.after.revision,
                    )
                finally:
                    self._queue.task_done()
        finally:
            self._closed = True
            self._discard_pending()

    def _discard_pending(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

    def _stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._discard_pending()
        if self._task is not asyncio.current_task():
            self._task.cancel()

    def _done(self, task: asyncio.Task[None]) -> None:
        self._closed = True
        self._discard_pending()
        if not task.cancelled():
            task.exception()
        self._finished(self)

    async def drain(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        self._stop()
        if self._task is not asyncio.current_task():
            await asyncio.gather(self._task, return_exceptions=True)


class SessionEvents:
    """Session-owned subscribers; publication never waits for consumer work."""

    def __init__(self) -> None:
        self._subscriptions: set[Subscription] = set()
        self._closed = False

    def subscribe(
        self,
        kinds: tuple[type[SessionEvent], ...],
        callback: Callable[[SessionEvent], Awaitable[None]],
        *,
        capacity: int = 64,
        latest: bool = False,
    ) -> Subscription:
        if self._closed:
            raise RuntimeError("Session events are closed.")
        if capacity < 1:
            raise ValueError("A subscription needs a positive capacity.")
        subscription = Subscription(
            kinds,
            callback,
            capacity=capacity,
            latest=latest,
            finished=self._subscriptions.discard,
        )
        self._subscriptions.add(subscription)
        return subscription

    def publish(self, event: SessionEvent) -> None:
        if not self._closed:
            for subscription in tuple(self._subscriptions):
                subscription.offer(event)

    async def drain(self) -> None:
        await asyncio.gather(*(item.drain() for item in tuple(self._subscriptions)))

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(*(item.close() for item in tuple(self._subscriptions)))
