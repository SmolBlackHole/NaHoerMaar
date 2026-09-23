# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persisted listening ownership, confirmed plays and recovery intent."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Literal
from uuid import UUID, uuid4

from nahormaar_backend.domain.identity import Contributor
from .queue import Outcome, Queue, QueueOrigin
from .radio import ManualStrategy, RadioStrategy


DEFAULT_CROSSFADE_SECONDS = 7


class PlaybackIntent(StrEnum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class Receipt:
    request_id: UUID
    session_id: UUID
    fingerprint: str
    actor_id: UUID | None
    outcome: Outcome


class PlaybackEndReason(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ListeningSession:
    """Stable ownership and desired voice connection, not transport readiness."""

    id: UUID = field(default_factory=uuid4)
    channel_id: int | None = None
    volume: float = 1
    revision: int = 0
    queue_revision: int = 0
    crossfade_seconds: int = DEFAULT_CROSSFADE_SECONDS

    def __post_init__(self) -> None:
        if self.channel_id is not None and (
            type(self.channel_id) is not int or self.channel_id <= 0
        ):
            raise ValueError("A reconnect channel must be a positive integer.")
        if not isfinite(self.volume) or not 0 <= self.volume <= 1:
            raise ValueError("Session volume must be finite and between 0 and 1.")
        if not 0 <= self.queue_revision <= self.revision:
            raise ValueError("Invalid session revisions.")
        if type(self.crossfade_seconds) is not int or self.crossfade_seconds not in (
            0,
            3,
            4,
            5,
            6,
            7,
        ):
            raise ValueError("Unsupported crossfade duration.")


@dataclass(frozen=True, slots=True)
class PlaybackRecord:
    """One confirmed play, independent of queue membership or output attempts.

    The application creates this only after confirmed audio output. Its ID
    survives seek, retry and restoration; an explicit replay gets a new ID.
    """

    session_id: UUID
    track_id: UUID
    entry_id: UUID
    started_at: datetime
    added_by: Contributor | None = None
    origin: QueueOrigin = QueueOrigin.MANUAL
    ended_at: datetime | None = None
    end_reason: PlaybackEndReason | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.started_at.utcoffset() is None:
            raise ValueError("Playback start needs a timezone-aware timestamp.")
        if type(self.origin) is not QueueOrigin:
            raise ValueError("Playback origin must be a QueueOrigin value.")
        if (self.ended_at is None) != (self.end_reason is None):
            raise ValueError("Playback end time and reason must be supplied together.")
        if self.ended_at is not None:
            if self.ended_at.utcoffset() is None:
                raise ValueError("Playback end needs a timezone-aware timestamp.")
            if self.ended_at < self.started_at:
                raise ValueError("Playback cannot end before it started.")
            if type(self.end_reason) is not PlaybackEndReason:
                raise ValueError(
                    "Playback end reason must be a PlaybackEndReason value."
                )


@dataclass(frozen=True, slots=True)
class PlaybackCheckpoint:
    """Measured progress and intent, including a not-yet-confirmed current entry.

    Entry attribution is retained when the occurrence leaves the upcoming queue.
    A play ID is present only after that logical play has been confirmed.
    """

    session_id: UUID
    intent: PlaybackIntent = PlaybackIntent.STOPPED
    entry_id: UUID | None = None
    track_id: UUID | None = None
    play_id: UUID | None = None
    position_seconds: float = 0
    added_by: Contributor | None = None
    origin: QueueOrigin = QueueOrigin.MANUAL

    def __post_init__(self) -> None:
        if type(self.intent) is not PlaybackIntent:
            raise ValueError("Playback intent must be a PlaybackIntent value.")
        if type(self.origin) is not QueueOrigin:
            raise ValueError("Checkpoint origin must be a QueueOrigin value.")
        if not isfinite(self.position_seconds) or self.position_seconds < 0:
            raise ValueError("Checkpoint position must be finite and non-negative.")
        if self.intent is PlaybackIntent.STOPPED:
            if (
                self.entry_id is not None
                or self.track_id is not None
                or self.play_id is not None
                or self.position_seconds != 0
                or self.added_by is not None
                or self.origin is not QueueOrigin.MANUAL
            ):
                raise ValueError(
                    "A stopped checkpoint has no current entry or progress."
                )
        elif self.entry_id is None or self.track_id is None:
            raise ValueError(
                "Playing or paused intent requires a current entry and track."
            )


class PlaybackPhase(StrEnum):
    IDLE = "idle"
    RESOLVING = "resolving"
    STARTING = "starting"
    PLAYING = "playing"
    PAUSED = "paused"
    SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class Preparation:
    entry_id: UUID
    id: UUID = field(default_factory=uuid4)
    ready: bool = False


@dataclass(frozen=True, slots=True)
class PlaybackRuntime:
    """Technical identities only; durable track/progress/intent live in checkpoint."""

    phase: PlaybackPhase = PlaybackPhase.IDLE
    attempt_id: UUID | None = None
    connection_id: UUID | None = None
    joining_id: UUID | None = None
    start_queue_on_join: bool = False
    retries: int = 0
    duration_seconds: float | None = None
    waiting_for_queue: bool = False
    preparation: Preparation | None = None
    failed_preparation: UUID | None = None
    preparation_retries: int = 0
    outgoing_play_id: UUID | None = None
    transition_id: UUID | None = None
    failed_entry_ids: frozenset[UUID] = frozenset()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    settings: ListeningSession
    queue: Queue
    checkpoint: PlaybackCheckpoint
    history: tuple[PlaybackRecord, ...] = ()
    strategy: ManualStrategy | RadioStrategy = field(default_factory=ManualStrategy)
    playback: PlaybackRuntime = field(default_factory=PlaybackRuntime)


type SessionAction = Literal[
    "queue.added",
    "queue.removed",
    "queue.reordered",
    "queue.cleared",
    "queue.restored",
    "playback.play",
    "playback.pause",
    "playback.skip",
    "playback.stop",
    "playback.seek",
    "playback.volume",
    "playback.crossfade",
    "connection.join",
    "connection.leave",
    "radio.started",
    "radio.stopped",
    "radio.retried",
    "session.updated",
]


@dataclass(frozen=True, slots=True)
class SessionChanged:
    before: SessionSnapshot
    after: SessionSnapshot
    action: SessionAction
    outcome: Outcome
    request_id: UUID | None = None
