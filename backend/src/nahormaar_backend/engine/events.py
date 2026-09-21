# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded post-commit delivery. Consumers own their tasks, never the publisher."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

_LOGGER = logging.getLogger(__name__)


class EventBus[T]:
    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue[T | None], bool] = {}
        self._closed = False

    @asynccontextmanager
    async def subscribe(
        self, *, capacity: int = 64, latest: bool = False
    ) -> AsyncGenerator[asyncio.Queue[T | None]]:
        if self._closed:
            raise RuntimeError("Event bus is closed.")
        if capacity < 1:
            raise ValueError("Subscriber capacity must be positive.")
        queue: asyncio.Queue[T | None] = asyncio.Queue(capacity)
        self._subscribers[queue] = latest
        try:
            yield queue
        finally:
            self._subscribers.pop(queue, None)

    def publish(self, event: T) -> None:
        for queue, latest in tuple(self._subscribers.items()):
            if queue.full():
                if not latest:
                    _LOGGER.error("engine.events.overflow: slow subscriber detached")
                    self._subscribers.pop(queue)
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(None)
                    continue
                queue.get_nowait()
            queue.put_nowait(event)

    def close(self) -> None:
        self._closed = True
        for queue in self._subscribers:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self._subscribers.clear()
