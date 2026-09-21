# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from nahormaar_backend.application.events import (
    PlaybackChanged,
    QueueChanged,
    RadioChanged,
    SessionEvent,
    SessionEvents,
)
from nahormaar_backend.application.status import PlaybackStatus
from nahormaar_backend.domain.models import PlayerSnapshot


def status(revision: int) -> PlaybackStatus:
    return PlaybackStatus(PlayerSnapshot(), None, None, 1, None, revision=revision)


def change(revision: int) -> QueueChanged:
    return QueueChanged(status(revision - 1), status(revision))


def test_committed_facts_are_immutable() -> None:
    event = change(1)
    assert event.before.revision == 0 and event.after.revision == 1
    with pytest.raises(FrozenInstanceError):
        event.__setattr__("after", status(2))


def test_subscribers_filter_types_and_preserve_order() -> None:
    async def scenario() -> None:
        bus = SessionEvents()
        selected: list[SessionEvent] = []
        all_events: list[SessionEvent] = []

        async def remember(event: SessionEvent) -> None:
            await asyncio.sleep(0)
            selected.append(event)

        async def remember_all(event: SessionEvent) -> None:
            all_events.append(event)

        subscription = bus.subscribe((QueueChanged, RadioChanged), remember)
        bus.subscribe((QueueChanged, PlaybackChanged, RadioChanged), remember_all)
        events = (
            change(1),
            PlaybackChanged(status(1), status(2)),
            RadioChanged(status(2), status(3)),
            change(4),
        )
        for event in events:
            bus.publish(event)
        await bus.drain()
        assert selected == [events[0], events[2], events[3]]
        assert all_events == list(events)
        assert not subscription.closed
        await bus.close()
        assert subscription.closed

    asyncio.run(scenario())


def test_slow_subscriber_does_not_block_publication_or_other_consumers() -> None:
    async def scenario() -> None:
        bus = SessionEvents()
        entered, release = asyncio.Event(), asyncio.Event()
        received: list[int] = []

        async def slow(event: SessionEvent) -> None:
            entered.set()
            await release.wait()

        async def fast(event: SessionEvent) -> None:
            received.append(event.after.revision)

        slow_subscription = bus.subscribe((QueueChanged,), slow)
        fast_subscription = bus.subscribe((QueueChanged,), fast)
        bus.publish(change(1))
        await entered.wait()
        bus.publish(change(2))
        await fast_subscription.drain()
        assert received == [1, 2]
        draining = asyncio.create_task(slow_subscription.drain())
        await asyncio.sleep(0)
        assert not draining.done()
        release.set()
        await draining
        await bus.close()

    asyncio.run(scenario())


def test_callback_failure_is_logged_and_later_events_continue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        bus = SessionEvents()
        received: list[int] = []

        async def callback(event: SessionEvent) -> None:
            if event.after.revision == 1:
                raise ValueError("Optional consumer failed")
            received.append(event.after.revision)

        subscription = bus.subscribe((QueueChanged,), callback)
        bus.publish(change(1))
        bus.publish(change(2))
        await bus.drain()
        assert received == [2] and not subscription.closed
        assert "session.events.subscriber_failed" in caplog.text
        assert "Optional consumer failed" in caplog.text
        await bus.close()

    asyncio.run(scenario())


def test_overflow_detaches_logs_and_bus_reaps_cancelled_consumer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        bus = SessionEvents()
        entered, cleanup, finish_cleanup = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        received: list[int] = []

        async def slow(event: SessionEvent) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await finish_cleanup.wait()

        async def fast(event: SessionEvent) -> None:
            received.append(event.after.revision)

        subscription = bus.subscribe((QueueChanged,), slow, capacity=1)
        fast_subscription = bus.subscribe((QueueChanged,), fast)
        bus.publish(change(1))
        await entered.wait()
        bus.publish(change(2))
        bus.publish(change(3))
        assert subscription.closed
        assert "session.events.subscriber_overflow" in caplog.text
        await cleanup.wait()
        bus.publish(change(4))
        await fast_subscription.drain()
        assert received == [1, 2, 3, 4]
        draining = asyncio.create_task(bus.drain())
        await asyncio.sleep(0)
        assert not draining.done()
        closing = asyncio.create_task(bus.close())
        await asyncio.sleep(0)
        assert not closing.done()
        finish_cleanup.set()
        await closing
        await draining
        await bus.close()

    asyncio.run(scenario())


def test_explicit_latest_mode_coalesces_pending_invalidations_without_detaching(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        bus = SessionEvents()
        entered, release = asyncio.Event(), asyncio.Event()
        received: list[int] = []

        async def callback(event: SessionEvent) -> None:
            received.append(event.after.revision)
            entered.set()
            await release.wait()

        subscription = bus.subscribe((QueueChanged,), callback, capacity=1, latest=True)
        bus.publish(change(1))
        await entered.wait()
        bus.publish(change(2))
        bus.publish(change(3))
        bus.publish(change(4))
        release.set()
        await bus.drain()
        assert received == [1, 4]
        assert not subscription.closed
        assert "subscriber_overflow" not in caplog.text
        await bus.close()

    asyncio.run(scenario())


def test_unsubscribe_discards_pending_work_and_close_rejects_new_delivery() -> None:
    async def scenario() -> None:
        bus = SessionEvents()
        called: list[SessionEvent] = []

        async def callback(event: SessionEvent) -> None:
            called.append(event)

        subscription = bus.subscribe((QueueChanged,), callback)
        bus.publish(change(1))
        await subscription.close()
        await subscription.drain()
        bus.publish(change(2))
        await bus.drain()
        assert not called and subscription.closed
        await bus.close()
        bus.publish(change(3))
        with pytest.raises(RuntimeError, match="closed"):
            bus.subscribe((QueueChanged,), callback)
        assert not called

    asyncio.run(scenario())


def test_callback_can_close_its_own_subscription() -> None:
    async def scenario() -> None:
        bus = SessionEvents()
        received: list[int] = []

        async def callback(event: SessionEvent) -> None:
            received.append(event.after.revision)
            await subscription.close()

        subscription = bus.subscribe((QueueChanged,), callback)
        bus.publish(change(1))
        bus.publish(change(2))
        await subscription.drain()
        assert received == [1] and subscription.closed
        await bus.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("capacity", [0, -1])
def test_subscriptions_require_a_bounded_positive_capacity(capacity: int) -> None:
    async def scenario() -> None:
        bus = SessionEvents()

        async def callback(event: SessionEvent) -> None:
            pass

        with pytest.raises(ValueError, match="capacity"):
            bus.subscribe((QueueChanged,), callback, capacity=capacity)
        await bus.close()

    asyncio.run(scenario())
