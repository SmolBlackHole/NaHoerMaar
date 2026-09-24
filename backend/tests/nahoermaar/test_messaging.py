# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import dataclass

import pytest

from nahoermaar.messaging import (
    Command,
    Event,
    HandlerRegistrationError,
    MessageBus,
    MessageContext,
    MissingCommandHandlerError,
)


@dataclass(frozen=True, slots=True)
class Add(Command[int]):
    left: int
    right: int


@dataclass(frozen=True, slots=True)
class Added(Event):
    total: int


def test_command_has_exactly_one_awaited_handler_and_propagates_context() -> None:
    bus = MessageBus()
    contexts: list[MessageContext] = []

    async def handle(command: Add, context: MessageContext) -> int:
        await asyncio.sleep(0)
        contexts.append(context)
        return command.left + command.right

    bus.register_command(Add, handle)
    context = MessageContext()

    assert asyncio.run(bus.execute(Add(2, 3), context)) == 5
    assert contexts == [context]

    with pytest.raises(HandlerRegistrationError, match="Add"):
        bus.register_command(Add, handle)


def test_command_errors_are_not_swallowed() -> None:
    bus = MessageBus()

    async def fail(_command: Add, _context: MessageContext) -> int:
        raise RuntimeError("handler failed")

    bus.register_command(Add, fail)

    with pytest.raises(RuntimeError, match="handler failed"):
        asyncio.run(bus.execute(Add(1, 1)))

    with pytest.raises(MissingCommandHandlerError, match="Add"):
        asyncio.run(MessageBus().execute(Add(1, 1)))


def test_event_supports_zero_or_multiple_ordered_consumers() -> None:
    bus = MessageBus()
    calls: list[tuple[str, int]] = []

    async def first(event: Added, _context: MessageContext) -> None:
        calls.append(("first", event.total))

    async def second(event: Added, _context: MessageContext) -> None:
        calls.append(("second", event.total))

    asyncio.run(bus.publish(Added(1)))
    bus.subscribe(Added, first)
    bus.subscribe(Added, first)
    bus.subscribe(Added, second)
    asyncio.run(bus.publish(Added(5)))

    assert calls == [("first", 5), ("second", 5)]
