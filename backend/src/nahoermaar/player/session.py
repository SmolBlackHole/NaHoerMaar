# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded serial command mailbox for the shared player session."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from time import perf_counter
from typing import Protocol
from uuid import UUID

from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.messaging import MessageBus, MessageContext
from nahoermaar.users.domain import UserId

from .domain import (
    RECEIPT_LIFETIME,
    OperationId,
    OperationReceipt,
    PlayerError,
    PlayerErrorCode,
    PlayerState,
    RadioSeed,
)
from .events import (
    ApplyRadioCandidates,
    MutationReply,
    PlayerChanged,
    PlayerCommand,
    PlayerEvent,
    PlayerEventStream,
    RadioRefillRequested,
    TrackSelection,
    UndoQueue,
)
from .fsm import transition
from .repository import SessionRepository

_LOGGER = logging.getLogger(__name__)
_MAILBOX_CAPACITY = 128
type UnitFactory = Callable[[], UnitOfWork]


class RadioResolver(Protocol):
    async def radio(
        self,
        seed: RadioSeed,
        *,
        limit: int,
        continuation: str | None,
    ) -> tuple[tuple[TrackSelection, ...], str | None]: ...


class CatalogRadioResolver:
    """Translate persisted radio seeds through the canonical catalog."""

    __slots__ = ("_catalog",)

    def __init__(self, catalog: CatalogService) -> None:
        self._catalog = catalog

    async def radio(
        self,
        seed: RadioSeed,
        *,
        limit: int,
        continuation: str | None,
    ) -> tuple[tuple[TrackSelection, ...], str | None]:
        page = await self._catalog.radio(
            source_id=seed.track_source_id,
            snapshot_id=seed.discovery_snapshot_id,
            limit=limit,
            continuation=continuation,
        )
        return (
            tuple(
                TrackSelection(source.track_id, source.id) for source in page.entries
            ),
            page.continuation,
        )


@dataclass(slots=True)
class _Envelope:
    command: PlayerCommand
    context: MessageContext
    future: asyncio.Future[MutationReply]
    enqueued_at: float


class PlayerSession:
    """Own one aggregate and process every mutation in arrival order."""

    __slots__ = ("_bus", "_closed", "_mailbox", "_state", "_units", "_worker")

    def __init__(
        self,
        state: PlayerState,
        units: UnitFactory,
        bus: MessageBus,
    ) -> None:
        self._state = state
        self._units = units
        self._bus = bus
        self._mailbox: asyncio.Queue[_Envelope | None] = asyncio.Queue(
            maxsize=_MAILBOX_CAPACITY
        )
        self._closed = False
        self._worker = asyncio.create_task(
            self._run(),
            name=f"player-session-{state.session.id}",
        )

    @property
    def state(self) -> PlayerState:
        return self._state

    async def execute(
        self,
        command: PlayerCommand,
        context: MessageContext,
    ) -> MutationReply:
        if self._closed:
            _LOGGER.warning(
                "player.command_rejected command=%s session=%s reason=session_closed",
                type(command).__name__,
                command.session_id,
            )
            raise PlayerError(PlayerErrorCode.SESSION_CLOSED, 503)
        future = asyncio.get_running_loop().create_future()
        try:
            self._mailbox.put_nowait(
                _Envelope(command, context, future, perf_counter())
            )
        except asyncio.QueueFull as error:
            _LOGGER.warning(
                "player.command_rejected command=%s session=%s reason=mailbox_full "
                "mailbox_size=%d mailbox_capacity=%d actor=%s",
                type(command).__name__,
                command.session_id,
                self._mailbox.qsize(),
                _MAILBOX_CAPACITY,
                context.actor_id,
            )
            raise PlayerError(PlayerErrorCode.PLAYER_BUSY, 503) from error
        _LOGGER.debug(
            "player.command_enqueued command=%s session=%s mailbox_size=%d actor=%s",
            type(command).__name__,
            command.session_id,
            self._mailbox.qsize(),
            context.actor_id,
        )
        return await asyncio.shield(future)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _LOGGER.debug(
            "player.session_draining session=%s pending_commands=%d",
            self._state.session.id,
            self._mailbox.qsize(),
        )
        await self._mailbox.join()
        await self._mailbox.put(None)
        await self._worker

    async def _run(self) -> None:
        while True:
            envelope = await self._mailbox.get()
            try:
                if envelope is None:
                    return
                _LOGGER.debug(
                    "player.command_processing command=%s session=%s wait_ms=%.1f "
                    "mailbox_remaining=%d actor=%s",
                    type(envelope.command).__name__,
                    envelope.command.session_id,
                    (perf_counter() - envelope.enqueued_at) * 1000,
                    self._mailbox.qsize(),
                    envelope.context.actor_id,
                )
                try:
                    result = await self._mutate(envelope.command, envelope.context)
                except BaseException as error:
                    if not envelope.future.done():
                        envelope.future.set_exception(error)
                else:
                    if not envelope.future.done():
                        envelope.future.set_result(result)
            finally:
                self._mailbox.task_done()

    async def _mutate(
        self,
        command: PlayerCommand,
        context: MessageContext,
    ) -> MutationReply:
        now = datetime.now(UTC)
        fingerprint = _fingerprint(command)
        async with self._units() as work:
            repository = SessionRepository(work.session)
            await repository.prune(now)
            receipt = await repository.receipt(command.operation_id)
            if receipt is not None:
                if (
                    receipt.session_id != command.session_id
                    or receipt.command_type != type(command).__name__
                    or receipt.fingerprint != fingerprint
                ):
                    raise PlayerError(PlayerErrorCode.IDEMPOTENCY_CONFLICT, 409)
                await work.commit()
                _LOGGER.info(
                    "player.command_replayed command=%s session=%s operation=%s "
                    "actor=%s action=%s",
                    type(command).__name__,
                    command.session_id,
                    command.operation_id,
                    context.actor_id,
                    receipt.outcome.action.value,
                )
                return MutationReply(self._state, receipt.outcome, replayed=True)

            undo = (
                await repository.undo(command.undo_id)
                if isinstance(command, UndoQueue)
                else None
            )
            actor_id = (
                UserId(context.actor_id) if context.actor_id is not None else None
            )
            previous = self._state
            change = transition(previous, command, actor_id, now, undo=undo)
            _LOGGER.debug(
                "player.transition_applied command=%s session=%s revision_from=%d "
                "revision_to=%d queue_from=%d queue_to=%d events=%d action=%s",
                type(command).__name__,
                command.session_id,
                previous.session.revision,
                change.state.session.revision,
                len(previous.queue.entries),
                len(change.state.queue.entries),
                len(change.events),
                change.outcome.action.value,
            )
            await repository.save(change.state)
            if change.save_undo is not None:
                await repository.save_undo(change.save_undo)
            if change.consume_undo is not None:
                await repository.consume_undo(change.consume_undo)
            await repository.save_receipt(
                OperationReceipt(
                    command.operation_id,
                    command.session_id,
                    type(command).__name__,
                    fingerprint,
                    now,
                    now + RECEIPT_LIFETIME,
                    change.outcome,
                )
            )
            await work.commit()

        self._state = change.state
        for event in change.events:
            await self._publish(event, context)
        _LOGGER.info(
            "player.command_committed command=%s session=%s actor=%s "
            "revision=%d queue_revision=%d action=%s",
            type(command).__name__,
            command.session_id,
            context.actor_id,
            self._state.session.revision,
            self._state.session.queue_revision,
            change.outcome.action.value,
        )
        return MutationReply(self._state, change.outcome)

    async def _publish(
        self,
        event: PlayerEvent,
        context: MessageContext,
    ) -> None:
        try:
            await self._bus.publish(
                event,
                context.child(),
            )
        except Exception:
            _LOGGER.exception(
                "player.event_consumer_failed event=%s session=%s",
                type(event).__name__,
                self._state.session.id,
            )


class PlayerSessionManager:
    """Compose persistence, the mailbox and asynchronous radio provider work."""

    __slots__ = (
        "_bus",
        "_events",
        "_radio",
        "_radio_tasks",
        "_session",
        "_units",
    )

    def __init__(
        self,
        units: UnitFactory,
        bus: MessageBus,
        radio: RadioResolver,
    ) -> None:
        self._units = units
        self._bus = bus
        self._radio = radio
        self._events = PlayerEventStream()
        self._session: PlayerSession | None = None
        self._radio_tasks: set[asyncio.Task[None]] = set()

    @property
    def events(self) -> PlayerEventStream:
        return self._events

    @property
    def operational(self) -> bool:
        """Return whether the durable player session has been restored."""
        return self._session is not None

    @property
    def state(self) -> PlayerState:
        session = self._session
        if session is None:
            raise RuntimeError("PlayerSessionManager is not started.")
        return session.state

    async def start(self) -> None:
        now = datetime.now(UTC)
        async with self._units() as work:
            repository = SessionRepository(work.session)
            await repository.prune(now)
            state = await repository.load_default(now)
            await work.commit()
        self._session = PlayerSession(state, self._units, self._bus)
        run = state.radio
        if run is not None and run.active and run.request_id is not None:
            await self.refill(
                RadioRefillRequested(
                    state.session.id,
                    run.id,
                    run.generation,
                    run.request_id,
                    run.seed,
                    run.continuation,
                ),
                MessageContext(),
            )
        _LOGGER.info(
            "player.session_started session=%s revision=%d queue=%d radio=%s",
            state.session.id,
            state.session.revision,
            len(state.queue.entries),
            run.id if run is not None and run.active else None,
        )

    async def execute(
        self,
        command: PlayerCommand,
        context: MessageContext,
    ) -> MutationReply:
        session = self._session
        if session is None:
            raise RuntimeError("PlayerSessionManager is not started.")
        if command.session_id != session.state.session.id:
            raise PlayerError(PlayerErrorCode.INVALID_COMMAND, 404)
        return await session.execute(command, context)

    async def broadcast(self, event: PlayerChanged, context: MessageContext) -> None:
        self._events.publish(event, context, self.state)

    async def refill(
        self,
        event: RadioRefillRequested,
        context: MessageContext,
    ) -> None:
        _LOGGER.info(
            "player.radio_refill_scheduled session=%s run=%s request=%s "
            "generation=%s continuation=%s",
            event.session_id,
            event.run_id,
            event.request_id,
            event.generation,
            event.continuation is not None,
        )
        task = asyncio.create_task(
            self._resolve_radio(event, context),
            name=f"player-radio-{event.request_id}",
        )
        self._radio_tasks.add(task)
        task.add_done_callback(self._radio_tasks.discard)

    async def close(self) -> None:
        session = self._session
        if session is not None:
            await session.close()
            self._session = None
        tasks = tuple(self._radio_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._radio_tasks.clear()
        self._events.close()
        _LOGGER.info("player.session_closed")

    async def _resolve_radio(
        self,
        event: RadioRefillRequested,
        context: MessageContext,
    ) -> None:
        try:
            selections, continuation = await self._radio.radio(
                event.seed,
                limit=20,
                continuation=event.continuation,
            )
            _LOGGER.info(
                "player.radio_candidates_resolved session=%s run=%s request=%s "
                "candidates=%d continuation=%s",
                event.session_id,
                event.run_id,
                event.request_id,
                len(selections),
                continuation is not None,
            )
            command = ApplyRadioCandidates(
                event.session_id,
                OperationId(event.request_id),
                event.run_id,
                event.generation,
                event.request_id,
                selections,
                continuation,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _LOGGER.exception(
                "player.radio_provider_failed session=%s run=%s request=%s",
                event.session_id,
                event.run_id,
                event.request_id,
            )
            command = ApplyRadioCandidates(
                event.session_id,
                OperationId(event.request_id),
                event.run_id,
                event.generation,
                event.request_id,
                (),
                error=str(error) or type(error).__name__,
            )
        try:
            await self._bus.execute(
                command,
                context.child(),
            )
        except PlayerError as error:
            if error.code is not PlayerErrorCode.RADIO_CONFLICT:
                raise
            _LOGGER.info(
                "player.radio_result_discarded session=%s run=%s request=%s",
                event.session_id,
                event.run_id,
                event.request_id,
            )


def _fingerprint(command: PlayerCommand) -> bytes:
    payload = json.dumps(
        {"type": type(command).__name__, "payload": asdict(command)},
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    ).encode()
    return sha256(payload).digest()


def _json_value(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported command value: {type(value).__name__}")
