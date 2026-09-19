# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Immutable queue entries and player state."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from uuid import UUID, uuid4


class PlaybackState(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


class VoiceState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


@dataclass(frozen=True, slots=True)
class TrackMetadata:
    video_id: str | None = None
    title: str | None = None
    uploader: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None
    artist: str | None = None
    uploader_url: str | None = None


@dataclass(frozen=True, slots=True)
class QueueEntry:
    source_url: str
    id: UUID = field(default_factory=uuid4)
    video_id: str | None = None
    title: str | None = None
    uploader: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None
    artist: str | None = None
    uploader_url: str | None = None

    def __post_init__(self) -> None:
        if not self.source_url.strip():
            raise ValueError("A queue entry needs a source URL.")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("Duration must be finite and non-negative.")


HISTORY_LIMIT = 100


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    entry: QueueEntry
    played_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.played_at.utcoffset() is None:
            raise ValueError("Playback history needs a timezone-aware timestamp.")


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    state: PlaybackState = PlaybackState.IDLE
    current: QueueEntry | None = None
    upcoming: tuple[QueueEntry, ...] = ()
    voice_state: VoiceState = VoiceState.DISCONNECTED
    recently_played: tuple[HistoryEntry, ...] = ()

    def __post_init__(self) -> None:
        if (self.state is PlaybackState.IDLE) != (self.current is None):
            raise ValueError("Only an idle player can have no current entry.")
        entry_ids = [entry.id for entry in self.upcoming]
        if self.current is not None:
            entry_ids.append(self.current.id)
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("Queue entry IDs must be unique across the player.")
        history_ids = [item.id for item in self.recently_played]
        if len(history_ids) > HISTORY_LIMIT or len(history_ids) != len(
            set(history_ids)
        ):
            raise ValueError("Invalid playback history.")
