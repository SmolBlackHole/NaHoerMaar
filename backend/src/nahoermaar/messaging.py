# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed in-process command and event dispatch on the application's event loop."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

from .observability import (
    LogContext,
    current_actor_id,
    current_correlation_id,
    error_code,
    log_context,
)

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
    correlation_id: UUID = field(default_factory=current_correlation_id)
    causation_id: UUID | None = None
    actor_id: UUID | None = field(default_factory=current_actor_id)

    def child(self) -> MessageContext:
        """Create context for a message caused directly by this message."""
        return MessageContext(
            correlation_id=self.correlation_id,
            causation_id=self.message_id,
            actor_id=self.actor_id,
        )


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
        active_context = context or MessageContext()
        with log_context(_log_context(active_context)):
            try:
                handler = self._command_handlers[command_type]
            except KeyError as error:
                _LOGGER.error(
                    "message.command_missing_handler type=%s",
                    command_type.__name__,
                )
                raise MissingCommandHandlerError(command_type.__name__) from error
            started = time.perf_counter()
            _LOGGER.debug("message.command_started type=%s", command_type.__name__)
            try:
                result = await handler(command, active_context)
            except Exception as error:
                duration = _elapsed_ms(started)
                if hasattr(error, "code"):
                    _LOGGER.warning(
                        "message.command_rejected type=%s duration_ms=%.2f error_code=%s",
                        command_type.__name__,
                        duration,
                        error_code(error),
                    )
                else:
                    _LOGGER.exception(
                        "message.command_failed type=%s duration_ms=%.2f error_code=%s",
                        command_type.__name__,
                        duration,
                        error_code(error),
                    )
                raise
            _LOGGER.debug(
                "message.command_completed type=%s duration_ms=%.2f",
                command_type.__name__,
                _elapsed_ms(started),
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
        with log_context(_log_context(active_context)):
            started = time.perf_counter()
            _LOGGER.debug(
                "message.event_published type=%s consumers=%d",
                type(event).__name__,
                len(consumers),
            )
            for consumer in consumers:
                try:
                    await consumer(event, active_context)
                except Exception as error:
                    _LOGGER.exception(
                        "message.event_consumer_failed type=%s consumer=%s "
                        "duration_ms=%.2f error_code=%s",
                        type(event).__name__,
                        getattr(consumer, "__qualname__", type(consumer).__name__),
                        _elapsed_ms(started),
                        error_code(error),
                    )
                    raise
            _LOGGER.debug(
                "message.event_completed type=%s consumers=%d duration_ms=%.2f",
                type(event).__name__,
                len(consumers),
                _elapsed_ms(started),
            )


def _log_context(context: MessageContext) -> LogContext:
    return LogContext(
        message_id=context.message_id,
        correlation_id=context.correlation_id,
        causation_id=context.causation_id,
        actor_id=context.actor_id,
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
