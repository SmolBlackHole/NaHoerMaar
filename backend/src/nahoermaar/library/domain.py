# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Domain values and failures owned by the personal Library."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.users.domain import UserId

PlaylistId = NewType("PlaylistId", UUID)
PlaylistEntryId = NewType("PlaylistEntryId", UUID)

MAX_PLAYLIST_ENTRIES = 100
MAX_PLAYLIST_NAME_LENGTH = 100


class ReactionValue(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


class LibraryErrorCode(StrEnum):
    TRACK_NOT_FOUND = "library_track_not_found"
    TRACK_SOURCE_NOT_FOUND = "library_track_source_not_found"
    PLAYLIST_NOT_FOUND = "library_playlist_not_found"
    PLAYLIST_ENTRY_NOT_FOUND = "library_playlist_entry_not_found"
    PLAYLIST_REVISION_CONFLICT = "library_playlist_revision_conflict"
    PLAYLIST_CAPACITY_EXCEEDED = "library_playlist_capacity_exceeded"
    PLAYLIST_ORDER_INVALID = "library_playlist_order_invalid"


class LibraryError(RuntimeError):
    """Expected Library failure with a stable public code."""

    def __init__(self, code: LibraryErrorCode, status: int) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class TrackReaction:
    user_id: UserId
    track_id: TrackId
    value: ReactionValue
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _aware(self.created_at, "Reaction creation time")
        _aware(self.updated_at, "Reaction update time")
        if self.updated_at < self.created_at:
            raise ValueError("Reaction update cannot precede its creation.")


@dataclass(frozen=True, slots=True)
class Playlist:
    id: PlaylistId
    owner_id: UserId
    name: str
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _playlist_name(self.name)
        if self.revision < 0:
            raise ValueError("Playlist revision must be non-negative.")
        _aware(self.created_at, "Playlist creation time")
        _aware(self.updated_at, "Playlist update time")
        if self.updated_at < self.created_at:
            raise ValueError("Playlist update cannot precede its creation.")


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    id: PlaylistEntryId
    playlist_id: PlaylistId
    track_id: TrackId
    preferred_source_id: TrackSourceId | None
    added_by: UserId
    position: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ValueError("Playlist entry position must be non-negative.")
        _aware(self.created_at, "Playlist entry creation time")


@dataclass(frozen=True, slots=True)
class PlaylistTrackSelection:
    track_id: TrackId
    preferred_source_id: TrackSourceId | None = None


def playlist_name(value: str) -> str:
    """Normalize and validate one user-visible playlist name."""
    normalized = value.strip()
    _playlist_name(normalized)
    return normalized


def _playlist_name(value: str) -> None:
    if not value or value != value.strip() or len(value) > MAX_PLAYLIST_NAME_LENGTH:
        raise ValueError(
            "Playlist name must be non-empty, trimmed and at most "
            f"{MAX_PLAYLIST_NAME_LENGTH} characters."
        )


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")
