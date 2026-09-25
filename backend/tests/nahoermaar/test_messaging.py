# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import logging
from dataclasses import dataclass
from uuid import uuid4

import pytest

from nahoermaar.messaging import (
    Command,
    Event,
    HandlerRegistrationError,
    MessageBus,
    MessageContext,
    MissingCommandHandlerError,
)
from nahoermaar.observability import ContextFilter, LogContext, log_context
from nahoermaar.operations.logs import RecentLogBuffer


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


def test_child_context_preserves_chain_and_records_direct_cause() -> None:
    actor_id = uuid4()
    parent = MessageContext(actor_id=actor_id)

    child = parent.child()

    assert child.message_id != parent.message_id
    assert child.correlation_id == parent.correlation_id
    assert child.causation_id == parent.message_id
    assert child.actor_id == actor_id


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


def test_bus_logs_correlated_duration_and_unexpected_failure() -> None:
    bus = MessageBus()

    async def fail(_command: Add, _context: MessageContext) -> int:
        raise RuntimeError("handler failed")

    bus.register_command(Add, fail)
    request_id = "request-1"
    correlation_id = uuid4()
    actor_id = uuid4()
    logs = RecentLogBuffer()
    logs.addFilter(ContextFilter())
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(logs)
    try:
        with log_context(
            LogContext(
                request_id=request_id,
                correlation_id=correlation_id,
                actor_id=actor_id,
            )
        ):
            with pytest.raises(RuntimeError, match="handler failed"):
                asyncio.run(bus.execute(Add(1, 1)))
    finally:
        root.removeHandler(logs)
        root.setLevel(previous_level)

    entries = logs.entries()
    failed = next(
        entry for entry in entries if "message.command_failed" in entry.message
    )
    assert "duration_ms=" in failed.message
    assert "error_code=RuntimeError" in failed.message
    assert failed.request_id == request_id
    assert failed.correlation_id == correlation_id
    assert failed.causation_id is None
    assert failed.actor_id == actor_id
    assert failed.message_id is not None
