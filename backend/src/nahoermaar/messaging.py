# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed in-process command and event dispatch on the application's event loop."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

_LOGGER = logging.getLogger(__name__)


class Command[ResultT]:
    """Marker carrying the result type returned by one command handler."""

    __slots__ = ()


class Event:
    """Marker for a fact that zero or more consumers may observe."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Correlation metadata shared by commands and their resulting events."""

    message_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)
    actor_id: UUID | None = None


class HandlerRegistrationError(RuntimeError):
    """Raised when a command type would receive more than one handler."""


class MissingCommandHandlerError(LookupError):
    """Raised when execution targets an unregistered command type."""


type _StoredHandler = Callable[[object, MessageContext], Awaitable[object]]
type _StoredEventHandler = Callable[[Event, MessageContext], Awaitable[None]]


class MessageBus:
    """Dispatch application messages without hiding scheduling or failures."""

    __slots__ = ("_command_handlers", "_event_handlers")

    def __init__(self) -> None:
        self._command_handlers: dict[type[object], _StoredHandler] = {}
        self._event_handlers: dict[type[Event], list[_StoredEventHandler]] = {}

    def register_command[CommandT, ResultT](
        self,
        command_type: type[CommandT],
        handler: Callable[[CommandT, MessageContext], Awaitable[ResultT]],
    ) -> None:
        """Register the sole handler for a command type."""
        if command_type in self._command_handlers:
            raise HandlerRegistrationError(command_type.__name__)
        self._command_handlers[command_type] = cast(_StoredHandler, handler)

    def subscribe[EventT: Event](
        self,
        event_type: type[EventT],
        handler: Callable[[EventT, MessageContext], Awaitable[None]],
    ) -> None:
        """Append one consumer for an event type."""
        consumers = self._event_handlers.setdefault(event_type, [])
        stored = cast(_StoredEventHandler, handler)
        if stored not in consumers:
            consumers.append(stored)

    async def execute[ResultT](
        self,
        command: Command[ResultT],
        context: MessageContext | None = None,
    ) -> ResultT:
        """Await the command handler on the caller's event loop."""
        command_type = type(command)
        try:
            handler = self._command_handlers[command_type]
        except KeyError as error:
            raise MissingCommandHandlerError(command_type.__name__) from error
        active_context = context or MessageContext()
        _LOGGER.debug(
            "message.command_started type=%s message_id=%s correlation_id=%s actor_id=%s",
            command_type.__name__,
            active_context.message_id,
            active_context.correlation_id,
            active_context.actor_id,
        )
        result = await handler(command, active_context)
        _LOGGER.debug(
            "message.command_completed type=%s message_id=%s correlation_id=%s",
            command_type.__name__,
            active_context.message_id,
            active_context.correlation_id,
        )
        return cast(ResultT, result)

    async def publish(
        self,
        event: Event,
        context: MessageContext | None = None,
    ) -> None:
        """Await every registered consumer in registration order."""
        active_context = context or MessageContext()
        consumers = tuple(self._event_handlers.get(type(event), ()))
        _LOGGER.debug(
            "message.event_published type=%s consumers=%d message_id=%s correlation_id=%s",
            type(event).__name__,
            len(consumers),
            active_context.message_id,
            active_context.correlation_id,
        )
        for consumer in consumers:
            await consumer(event, active_context)
