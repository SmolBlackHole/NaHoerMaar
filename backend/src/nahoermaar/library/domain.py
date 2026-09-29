# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Domain values and failures owned by the personal Library."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NewType
from uuid import UUID

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.users.domain import UserId

PlaylistId = NewType("PlaylistId", UUID)
PlaylistEntryId = NewType("PlaylistEntryId", UUID)
PlaylistEntryUndoId = NewType("PlaylistEntryUndoId", UUID)

MAX_PLAYLIST_ENTRIES = 1_000
MAX_PLAYLIST_MUTATION_ENTRIES = 100
MAX_PLAYLIST_NAME_LENGTH = 100
PLAYLIST_ENTRY_UNDO_LIFETIME = timedelta(seconds=12)


class ReactionValue(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


class PlaylistVisibility(StrEnum):
    PRIVATE = "private"
    COLLABORATORS = "collaborators"
    PUBLIC = "public"


class PlaylistAccess(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    READER = "reader"


class PlaylistScope(StrEnum):
    OWNED = "owned"
    SHARED = "shared"
    PUBLIC = "public"


class LibraryErrorCode(StrEnum):
    TRACK_NOT_FOUND = "library_track_not_found"
    TRACK_SOURCE_NOT_FOUND = "library_track_source_not_found"
    PLAYLIST_NOT_FOUND = "library_playlist_not_found"
    PLAYLIST_ENTRY_NOT_FOUND = "library_playlist_entry_not_found"
    PLAYLIST_REVISION_CONFLICT = "library_playlist_revision_conflict"
    PLAYLIST_CAPACITY_EXCEEDED = "library_playlist_capacity_exceeded"
    PLAYLIST_ORDER_INVALID = "library_playlist_order_invalid"
    PLAYLIST_ACCESS_DENIED = "library_playlist_access_denied"
    PLAYLIST_COLLABORATOR_INVALID = "library_playlist_collaborator_invalid"
    PLAYLIST_COLLABORATOR_EXISTS = "library_playlist_collaborator_exists"
    PLAYLIST_LINKED_READ_ONLY = "library_playlist_linked_read_only"
    PLAYLIST_UNDO_UNAVAILABLE = "library_playlist_undo_unavailable"
    USER_NOT_FOUND = "library_user_not_found"


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
class PlaylistSource:
    provider_key: str
    external_id: str
    canonical_url: str
    last_attempt_at: datetime
    last_successful_sync_at: datetime
    last_error_code: str | None
    unavailable_entry_count: int
    truncated: bool

    def __post_init__(self) -> None:
        if (
            not self.provider_key
            or self.provider_key != self.provider_key.strip()
            or len(self.provider_key) > 64
        ):
            raise ValueError(
                "Playlist source provider key must be non-empty and trimmed."
            )
        if (
            not self.external_id
            or self.external_id != self.external_id.strip()
            or len(self.external_id) > 200
        ):
            raise ValueError(
                "Playlist source external ID must be non-empty and trimmed."
            )
        if (
            not self.canonical_url
            or self.canonical_url != self.canonical_url.strip()
            or len(self.canonical_url) > 2048
        ):
            raise ValueError("Playlist source URL must be non-empty and trimmed.")
        _aware(self.last_attempt_at, "Playlist source attempt time")
        _aware(self.last_successful_sync_at, "Playlist source success time")
        if self.last_successful_sync_at > self.last_attempt_at:
            raise ValueError(
                "Playlist source success cannot follow its latest attempt."
            )
        if self.last_error_code is not None and (
            not self.last_error_code
            or self.last_error_code != self.last_error_code.strip()
            or len(self.last_error_code) > 200
        ):
            raise ValueError(
                "Playlist source error code must be non-empty and trimmed."
            )
        if self.unavailable_entry_count < 0:
            raise ValueError("Unavailable playlist entries must be non-negative.")


@dataclass(frozen=True, slots=True)
class Playlist:
    id: PlaylistId
    owner_id: UserId
    name: str
    visibility: PlaylistVisibility
    source: PlaylistSource | None
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
class PlaylistEntryUndo:
    id: PlaylistEntryUndoId
    actor_id: UserId
    entry: PlaylistEntry
    removed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _aware(self.removed_at, "Playlist undo creation time")
        _aware(self.expires_at, "Playlist undo expiry time")
        if self.expires_at <= self.removed_at:
            raise ValueError("Playlist undo expiry must follow creation.")


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
