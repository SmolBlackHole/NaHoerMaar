# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Stable player, queue and radio values without infrastructure dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import NewType
from uuid import UUID, uuid4

from nahoermaar.catalog.domain import (
    DiscoverySnapshotId,
    MediaKind,
    TrackId,
    TrackSourceId,
)
from nahoermaar.users.domain import UserId

ListeningSessionId = NewType("ListeningSessionId", UUID)
TrackRequestId = NewType("TrackRequestId", UUID)
QueueEntryId = NewType("QueueEntryId", UUID)
RadioRunId = NewType("RadioRunId", UUID)
RadioCandidateId = NewType("RadioCandidateId", UUID)
UndoId = NewType("UndoId", UUID)
OperationId = NewType("OperationId", UUID)

RADIO_TARGET = 3
UNDO_LIFETIME = timedelta(seconds=12)
RECEIPT_LIFETIME = timedelta(hours=24)
DEFAULT_CROSSFADE_SECONDS = 7


class RequestOrigin(StrEnum):
    MANUAL = "manual"
    RADIO = "radio"


class RadioState(StrEnum):
    ACTIVE = "active"
    LOADING = "loading"
    WAITING = "waiting"


class PlaybackIntent(StrEnum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class VoiceConnectionPhase(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RETRYING = "retrying"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VoiceConnectionState:
    phase: VoiceConnectionPhase = VoiceConnectionPhase.DISCONNECTED
    channel_id: int | None = None
    attempt: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if self.channel_id is not None and self.channel_id <= 0:
            raise ValueError("Voice channel IDs must be positive.")
        if self.attempt < 0:
            raise ValueError("Voice connection attempts must be non-negative.")


class PlayerAction(StrEnum):
    QUEUE_ADDED = "queue.added"
    QUEUE_REMOVED = "queue.removed"
    QUEUE_MOVED = "queue.moved"
    QUEUE_CLEARED = "queue.cleared"
    QUEUE_RESTORED = "queue.restored"
    RADIO_STARTED = "radio.started"
    RADIO_STOPPED = "radio.stopped"
    RADIO_RETRIED = "radio.retried"
    RADIO_FILLED = "radio.filled"
    RADIO_FAILED = "radio.failed"
    PLAYBACK_PLAYED = "playback.played"
    PLAYBACK_PAUSED = "playback.paused"
    PLAYBACK_SKIPPED = "playback.skipped"
    PLAYBACK_STOPPED = "playback.stopped"
    PLAYBACK_SEEKED = "playback.seeked"
    PLAYBACK_COMPLETED = "playback.completed"
    PLAYBACK_FAILED = "playback.failed"
    PLAYBACK_CHECKPOINTED = "playback.checkpointed"
    VOLUME_CHANGED = "playback.volume_changed"
    CROSSFADE_CHANGED = "playback.crossfade_changed"
    VOICE_JOINED = "voice.joined"
    VOICE_LEFT = "voice.left"


class PlayerErrorCode(StrEnum):
    ACTOR_REQUIRED = "actor_required"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INVALID_COMMAND = "invalid_command"
    NOTHING_TO_PLAY = "nothing_to_play"
    NOTHING_PLAYING = "nothing_playing"
    PLAYER_BUSY = "player_busy"
    QUEUE_CONFLICT = "queue_conflict"
    QUEUE_ENTRY_NOT_FOUND = "queue_entry_not_found"
    RADIO_CONFLICT = "radio_conflict"
    RADIO_NOT_ACTIVE = "radio_not_active"
    SESSION_CLOSED = "session_closed"
    UNDO_UNAVAILABLE = "undo_unavailable"


class PlayerError(RuntimeError):
    """Expected player failure with a stable API code."""

    def __init__(self, code: PlayerErrorCode, status: int = 400) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status


def _aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class ListeningSession:
    id: ListeningSessionId
    revision: int = 0
    queue_revision: int = 0
    channel_id: int | None = None
    volume: float = 1.0
    crossfade_seconds: int = DEFAULT_CROSSFADE_SECONDS
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.revision < 0 or not 0 <= self.queue_revision <= self.revision:
            raise ValueError("Session revisions are invalid.")
        if self.channel_id is not None and self.channel_id <= 0:
            raise ValueError("Voice channel IDs must be positive.")
        if not isfinite(self.volume) or not 0 <= self.volume <= 1:
            raise ValueError("Volume must be finite and between zero and one.")
        if self.crossfade_seconds not in {0, 3, 4, 5, 6, 7}:
            raise ValueError("Unsupported crossfade duration.")
        if self.created_at is not None:
            _aware(self.created_at, "Session creation")
        if self.updated_at is not None:
            _aware(self.updated_at, "Session update")


@dataclass(frozen=True, slots=True)
class TrackRequest:
    id: TrackRequestId
    session_id: ListeningSessionId
    track_id: TrackId
    source_id: TrackSourceId | None
    requested_at: datetime
    origin: RequestOrigin
    requested_by: UserId | None = None
    radio_run_id: RadioRunId | None = None

    def __post_init__(self) -> None:
        _aware(self.requested_at, "Track request time")
        if self.origin is RequestOrigin.MANUAL:
            if self.requested_by is None or self.radio_run_id is not None:
                raise ValueError("Manual requests need one user and no radio run.")
        elif self.requested_by is not None or self.radio_run_id is None:
            raise ValueError("Radio requests need one radio run and no user.")


@dataclass(frozen=True, slots=True)
class QueueEntry:
    id: QueueEntryId
    session_id: ListeningSessionId
    request: TrackRequest
    position: int

    def __post_init__(self) -> None:
        if self.request.session_id != self.session_id:
            raise ValueError("Queue entry and request must share a session.")
        if self.position < 0:
            raise ValueError("Queue position must be non-negative.")

    @property
    def track_id(self) -> TrackId:
        return self.request.track_id


@dataclass(frozen=True, slots=True)
class Queue:
    session_id: ListeningSessionId
    revision: int = 0
    entries: tuple[QueueEntry, ...] = ()

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("Queue revision must be non-negative.")
        if type(self.entries) is not tuple:
            raise ValueError("Queue entries must be an immutable tuple.")
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Queue entry IDs must be unique.")
        if any(entry.session_id != self.session_id for entry in self.entries):
            raise ValueError("Queue entries must belong to their queue.")
        if tuple(entry.position for entry in self.entries) != tuple(
            range(len(self.entries))
        ):
            raise ValueError("Queue positions must be contiguous.")

    def positioned(self, entries: tuple[QueueEntry, ...], *, changed: bool) -> Queue:
        return Queue(
            self.session_id,
            self.revision + int(changed),
            tuple(
                replace(entry, position=index) for index, entry in enumerate(entries)
            ),
        )


@dataclass(frozen=True, slots=True)
class RadioSeed:
    kind: MediaKind
    track_source_id: TrackSourceId | None = None
    discovery_snapshot_id: DiscoverySnapshotId | None = None

    def __post_init__(self) -> None:
        if self.kind is MediaKind.TRACK:
            valid = (
                self.track_source_id is not None and self.discovery_snapshot_id is None
            )
        else:
            valid = (
                self.discovery_snapshot_id is not None and self.track_source_id is None
            )
        if not valid:
            raise ValueError("Radio seed identity does not match its media kind.")


@dataclass(frozen=True, slots=True)
class RadioCandidate:
    id: RadioCandidateId
    run_id: RadioRunId
    track_id: TrackId
    source_id: TrackSourceId
    position: int

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ValueError("Radio candidate position must be non-negative.")


@dataclass(frozen=True, slots=True)
class RadioRun:
    id: RadioRunId
    session_id: ListeningSessionId
    seed: RadioSeed
    initiated_by: UserId
    started_at: datetime
    generation: UUID
    state: RadioState = RadioState.ACTIVE
    continuation: str | None = None
    request_id: UUID | None = None
    candidates: tuple[RadioCandidate, ...] = ()
    excluded_track_ids: frozenset[TrackId] = frozenset()
    error: str | None = None
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        _aware(self.started_at, "Radio start")
        if self.ended_at is not None:
            _aware(self.ended_at, "Radio end")
            if self.ended_at < self.started_at:
                raise ValueError("Radio cannot end before it starts.")
        if type(self.candidates) is not tuple:
            raise ValueError("Radio candidates must be an immutable tuple.")
        if any(candidate.run_id != self.id for candidate in self.candidates):
            raise ValueError("Radio candidates must belong to their run.")
        if tuple(candidate.position for candidate in self.candidates) != tuple(
            range(len(self.candidates))
        ):
            raise ValueError("Radio candidate positions must be contiguous.")
        if self.state is RadioState.LOADING and self.request_id is None:
            raise ValueError("Loading radio needs a request identity.")
        if self.state is not RadioState.LOADING and self.request_id is not None:
            raise ValueError("Only loading radio may retain a request identity.")

    @property
    def active(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True, slots=True)
class PlaybackCheckpoint:
    session_id: ListeningSessionId
    intent: PlaybackIntent = PlaybackIntent.STOPPED
    request: TrackRequest | None = None
    position_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.request is not None and self.request.session_id != self.session_id:
            raise ValueError("Playback request must belong to the session.")
        if not isfinite(self.position_seconds) or self.position_seconds < 0:
            raise ValueError("Playback position must be finite and non-negative.")
        if self.intent is PlaybackIntent.STOPPED and (
            self.request is not None or self.position_seconds != 0
        ):
            raise ValueError("Stopped playback cannot retain an entry or position.")
        if self.intent is not PlaybackIntent.STOPPED and self.request is None:
            raise ValueError("Active playback needs a track request.")


@dataclass(frozen=True, slots=True)
class UndoGroup:
    entries: tuple[QueueEntry, ...]
    previous_id: QueueEntryId | None
    next_id: QueueEntryId | None


@dataclass(frozen=True, slots=True)
class QueueUndo:
    id: UndoId
    session_id: ListeningSessionId
    actor_id: UserId
    created_at: datetime
    expires_at: datetime
    groups: tuple[UndoGroup, ...]

    def __post_init__(self) -> None:
        _aware(self.created_at, "Undo creation")
        _aware(self.expires_at, "Undo expiry")
        if self.expires_at <= self.created_at:
            raise ValueError("Undo expiry must follow creation.")


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    action: PlayerAction
    added_count: int = 0
    removed_count: int = 0
    restored_count: int = 0
    skipped_count: int = 0
    entry_ids: tuple[QueueEntryId, ...] = ()
    undo_id: UndoId | None = None
    undo_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    operation_id: OperationId
    session_id: ListeningSessionId
    command_type: str
    fingerprint: bytes
    created_at: datetime
    expires_at: datetime
    outcome: MutationOutcome

    def __post_init__(self) -> None:
        if not self.command_type or self.command_type != self.command_type.strip():
            raise ValueError("Receipt command type must be non-empty and trimmed.")
        if not self.fingerprint:
            raise ValueError("Receipt fingerprint must not be empty.")
        _aware(self.created_at, "Receipt creation")
        _aware(self.expires_at, "Receipt expiry")
        if self.expires_at <= self.created_at:
            raise ValueError("Receipt expiry must follow creation.")


@dataclass(frozen=True, slots=True)
class PlayerState:
    session: ListeningSession
    queue: Queue
    checkpoint: PlaybackCheckpoint
    radio: RadioRun | None = None

    def __post_init__(self) -> None:
        identifier = self.session.id
        if (
            self.queue.session_id != identifier
            or self.checkpoint.session_id != identifier
        ):
            raise ValueError("Player aggregates must share a session identity.")
        if self.radio is not None and self.radio.session_id != identifier:
            raise ValueError("Radio run must belong to the player session.")

    @classmethod
    def empty(
        cls,
        session_id: ListeningSessionId,
        now: datetime,
    ) -> PlayerState:
        _aware(now, "Session creation")
        session = ListeningSession(
            session_id,
            created_at=now,
            updated_at=now,
        )
        return cls(
            session,
            Queue(session_id),
            PlaybackCheckpoint(session_id),
        )


def new_request(
    session_id: ListeningSessionId,
    track_id: TrackId,
    source_id: TrackSourceId | None,
    requested_at: datetime,
    *,
    actor_id: UserId | None = None,
    radio_run_id: RadioRunId | None = None,
) -> TrackRequest:
    origin = RequestOrigin.RADIO if radio_run_id is not None else RequestOrigin.MANUAL
    return TrackRequest(
        TrackRequestId(uuid4()),
        session_id,
        track_id,
        source_id,
        requested_at,
        origin,
        actor_id,
        radio_run_id,
    )
