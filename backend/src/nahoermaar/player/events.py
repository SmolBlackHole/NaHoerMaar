# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed player commands, committed events and bounded live subscriptions."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from uuid import UUID

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.messaging import Command, Event, MessageContext
from nahoermaar.users.domain import UserId

from .domain import (
    ListeningSessionId,
    MutationOutcome,
    OperationId,
    PlayerState,
    QueueEntryId,
    RadioRunId,
    RadioSeed,
    UndoId,
    VoiceConnectionState,
)


@dataclass(frozen=True, slots=True)
class TrackSelection:
    track_id: TrackId
    source_id: TrackSourceId | None = None


@dataclass(frozen=True, slots=True)
class MutationReply:
    state: PlayerState
    outcome: MutationOutcome
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class AddTracks(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    selections: tuple[TrackSelection, ...]
    skip_duplicates: bool = False


@dataclass(frozen=True, slots=True)
class RemoveQueueEntry(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    entry_id: QueueEntryId
    expected_queue_revision: int


@dataclass(frozen=True, slots=True)
class MoveQueueEntry(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    entry_id: QueueEntryId
    before_entry_id: QueueEntryId | None
    expected_queue_revision: int


@dataclass(frozen=True, slots=True)
class ClearQueue(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    expected_queue_revision: int
    requested_by: UserId | None = None


@dataclass(frozen=True, slots=True)
class UndoQueue(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    undo_id: UndoId


@dataclass(frozen=True, slots=True)
class StartRadio(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    seed: RadioSeed
    expected_generation: UUID | None = None


@dataclass(frozen=True, slots=True)
class StopRadio(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    expected_generation: UUID


@dataclass(frozen=True, slots=True)
class RetryRadio(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    expected_generation: UUID


@dataclass(frozen=True, slots=True)
class ApplyRadioCandidates(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    run_id: RadioRunId
    generation: UUID
    request_id: UUID
    selections: tuple[TrackSelection, ...]
    continuation: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Play(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId


@dataclass(frozen=True, slots=True)
class Pause(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId


@dataclass(frozen=True, slots=True)
class Skip(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId


@dataclass(frozen=True, slots=True)
class StopPlayback(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId


@dataclass(frozen=True, slots=True)
class Seek(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    seconds: float


@dataclass(frozen=True, slots=True)
class SetVolume(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    volume: float


@dataclass(frozen=True, slots=True)
class SetCrossfade(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    seconds: int


@dataclass(frozen=True, slots=True)
class JoinVoice(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    channel_id: int


@dataclass(frozen=True, slots=True)
class LeaveVoice(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId


@dataclass(frozen=True, slots=True)
class CompletePlayback(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    request_id: UUID


@dataclass(frozen=True, slots=True)
class FailPlayback(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    request_id: UUID


@dataclass(frozen=True, slots=True)
class CheckpointPlayback(Command[MutationReply]):
    session_id: ListeningSessionId
    operation_id: OperationId
    request_id: UUID
    position_seconds: float


type PlayerCommand = (
    AddTracks
    | RemoveQueueEntry
    | MoveQueueEntry
    | ClearQueue
    | UndoQueue
    | StartRadio
    | StopRadio
    | RetryRadio
    | ApplyRadioCandidates
    | Play
    | Pause
    | Skip
    | StopPlayback
    | Seek
    | SetVolume
    | SetCrossfade
    | JoinVoice
    | LeaveVoice
    | CompletePlayback
    | FailPlayback
    | CheckpointPlayback
)


@dataclass(frozen=True, slots=True)
class QueueChanged(Event):
    session_id: ListeningSessionId
    queue_revision: int


@dataclass(frozen=True, slots=True)
class RadioRefillRequested(Event):
    session_id: ListeningSessionId
    run_id: RadioRunId
    generation: UUID
    request_id: UUID
    seed: RadioSeed
    continuation: str | None


@dataclass(frozen=True, slots=True)
class PlayerChanged(Event):
    session_id: ListeningSessionId
    revision: int
    operation_id: OperationId
    outcome: MutationOutcome


@dataclass(frozen=True, slots=True)
class VoiceConnectionChanged(Event):
    """Transient result of reconciling the persisted voice target."""

    session_id: ListeningSessionId
    connection: VoiceConnectionState


@dataclass(frozen=True, slots=True)
class PlayerStateChange:
    event: PlayerChanged
    context: MessageContext
    state: PlayerState


@dataclass(frozen=True, slots=True)
class Reauthenticate:
    pass


type LivePlayerEvent = PlayerStateChange | Reauthenticate
type PlayerEvent = QueueChanged | RadioRefillRequested | PlayerChanged

_LOGGER = logging.getLogger(__name__)


class PlayerEventStream:
    """Fan committed player state out to bounded transient subscribers."""

    __slots__ = ("_closed", "_subscribers")

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[LivePlayerEvent | None]] = set()
        self._closed = False

    @asynccontextmanager
    async def subscribe(
        self, *, capacity: int = 16
    ) -> AsyncGenerator[asyncio.Queue[LivePlayerEvent | None]]:
        if self._closed:
            raise RuntimeError("Player event stream is closed.")
        if capacity < 1:
            raise ValueError("Subscriber capacity must be positive.")
        queue: asyncio.Queue[LivePlayerEvent | None] = asyncio.Queue(capacity)
        self._subscribers.add(queue)
        _LOGGER.debug(
            "player.events.subscriber_connected subscribers=%d capacity=%d",
            len(self._subscribers),
            capacity,
        )
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
            _LOGGER.debug(
                "player.events.subscriber_disconnected subscribers=%d",
                len(self._subscribers),
            )

    def publish(
        self, event: PlayerChanged, context: MessageContext, state: PlayerState
    ) -> None:
        _LOGGER.debug(
            "player.events.publish session=%s revision=%d action=%s subscribers=%d",
            event.session_id,
            event.revision,
            event.outcome.action.value,
            len(self._subscribers),
        )
        self._send(PlayerStateChange(event, context, state))

    def reauthenticate(self) -> None:
        _LOGGER.debug(
            "player.events.reauthenticate subscribers=%d", len(self._subscribers)
        )
        self._send(Reauthenticate())

    def close(self) -> None:
        self._closed = True
        _LOGGER.debug("player.events.closing subscribers=%d", len(self._subscribers))
        for queue in tuple(self._subscribers):
            self._disconnect(queue)
        self._subscribers.clear()

    def _send(self, event: LivePlayerEvent) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                _LOGGER.warning(
                    "player.events.slow_subscriber_disconnected subscribers=%d "
                    "capacity=%d queued=%d",
                    len(self._subscribers),
                    queue.maxsize,
                    queue.qsize(),
                )
                self._subscribers.discard(queue)
                self._disconnect(queue)
            else:
                queue.put_nowait(event)

    @staticmethod
    def _disconnect(queue: asyncio.Queue[LivePlayerEvent | None]) -> None:
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)
