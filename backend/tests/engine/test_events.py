# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio

import pytest

from nahormaar_backend.engine.events import EventBus


def test_ordered_delivery_detaches_slow_consumer_without_blocking_other_subscribers() -> (
    None
):
    async def scenario() -> None:
        bus = EventBus[int]()
        async with bus.subscribe(capacity=1) as slow, bus.subscribe() as healthy:
            bus.publish(1)
            bus.publish(2)
            assert await slow.get() is None
            assert [await healthy.get(), await healthy.get()] == [1, 2]
            bus.publish(3)
            assert slow.empty() and await healthy.get() == 3
            bus.close()
            assert await healthy.get() is None
        with pytest.raises(RuntimeError, match="closed"):
            async with bus.subscribe():
                pass

    asyncio.run(scenario())


def test_coalesced_invalidations_and_consumer_failure_are_isolated() -> None:
    async def scenario() -> None:
        bus = EventBus[int]()
        async with bus.subscribe(capacity=1, latest=True) as latest:
            with pytest.raises(ValueError, match="consumer"):
                async with bus.subscribe() as failing:
                    bus.publish(1)
                    assert await failing.get() == 1
                    raise ValueError("consumer failed")
            bus.publish(2)
            bus.publish(3)
            assert await latest.get() == 3
            bus.close()

    asyncio.run(scenario())
