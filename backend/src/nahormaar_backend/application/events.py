# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded snapshot delivery; a slow subscriber only needs the newest state."""

import asyncio


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
