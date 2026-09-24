# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed player commands, outcomes and committed domain events."""

from dataclasses import dataclass
from uuid import UUID

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.messaging import Command, Event
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
    action: str


type PlayerEvent = QueueChanged | RadioRefillRequested | PlayerChanged
