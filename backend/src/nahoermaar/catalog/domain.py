# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Canonical artists, tracks, provider sources and discovery snapshots."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from math import isfinite
from typing import NewType
from uuid import UUID

ArtistId = NewType("ArtistId", UUID)
TrackId = NewType("TrackId", UUID)
TrackSourceId = NewType("TrackSourceId", UUID)
DiscoverySnapshotId = NewType("DiscoverySnapshotId", UUID)


class ProviderName(StrEnum):
    """Provider namespaces persisted by the catalog."""

    YOUTUBE = "youtube"
    YOUTUBE_MUSIC = "youtube_music"


class MediaKind(StrEnum):
    TRACK = "track"
    PLAYLIST = "playlist"


class ObservationQuality(StrEnum):
    DISCOVERY = "discovery"
    DETAIL = "detail"

    @property
    def priority(self) -> int:
        return 1 if self is ObservationQuality.DETAIL else 0


class SourceAvailability(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class DiscoveryKind(StrEnum):
    SEARCH = "search"
    PLAYLIST = "playlist"


def _aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def _text(value: str, name: str, *, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(
            f"{name} must be non-empty, trimmed and at most {maximum} characters."
        )


def _optional_text(value: str | None, name: str, *, maximum: int) -> None:
    if value is not None:
        _text(value, name, maximum=maximum)


@dataclass(frozen=True, slots=True)
class ArtistSource:
    provider: ProviderName
    external_id: str
    observed_name: str
    first_seen_at: datetime
    checked_at: datetime

    def __post_init__(self) -> None:
        _text(self.external_id, "Artist external ID", maximum=200)
        _text(self.observed_name, "Observed artist name", maximum=200)
        _aware(self.first_seen_at, "Artist source first_seen_at")
        _aware(self.checked_at, "Artist source checked_at")
        if self.checked_at.astimezone(UTC) < self.first_seen_at.astimezone(UTC):
            raise ValueError("Artist source check cannot precede discovery.")


@dataclass(frozen=True, slots=True)
class Artist:
    id: ArtistId
    name: str
    created_at: datetime
    updated_at: datetime
    sources: tuple[ArtistSource, ...] = ()

    def __post_init__(self) -> None:
        _text(self.name, "Artist name", maximum=200)
        _aware(self.created_at, "Artist created_at")
        _aware(self.updated_at, "Artist updated_at")
        if self.updated_at.astimezone(UTC) < self.created_at.astimezone(UTC):
            raise ValueError("Artist update cannot precede creation.")
        if type(self.sources) is not tuple:
            raise ValueError("Artist sources must be an immutable tuple.")


@dataclass(frozen=True, slots=True)
class ArtistCredit:
    artist: Artist
    position: int

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 0:
            raise ValueError("Artist credit position must be non-negative.")


@dataclass(frozen=True, slots=True)
class TrackSource:
    id: TrackSourceId
    track_id: TrackId
    provider: ProviderName
    external_id: str
    source_url: str
    observed_title: str
    observed_artist: str | None
    observed_duration_seconds: float | None
    observed_artwork_url: str | None
    observed_album_title: str | None
    observed_release_date: date | None
    observed_isrc: str | None
    uploader_name: str | None
    uploader_url: str | None
    quality: ObservationQuality
    availability: SourceAvailability
    first_seen_at: datetime
    checked_at: datetime

    def __post_init__(self) -> None:
        _text(self.external_id, "Track external ID", maximum=200)
        _text(self.source_url, "Track source URL", maximum=2048)
        _text(self.observed_title, "Observed track title", maximum=500)
        _optional_text(self.observed_artist, "Observed artist", maximum=500)
        _optional_text(self.observed_artwork_url, "Observed artwork URL", maximum=2048)
        _optional_text(self.observed_album_title, "Observed album title", maximum=500)
        _optional_text(self.observed_isrc, "Observed ISRC", maximum=15)
        _optional_text(self.uploader_name, "Uploader name", maximum=200)
        _optional_text(self.uploader_url, "Uploader URL", maximum=2048)
        if self.observed_duration_seconds is not None and (
            not isfinite(self.observed_duration_seconds)
            or self.observed_duration_seconds <= 0
        ):
            raise ValueError("Observed duration must be positive and finite.")
        _aware(self.first_seen_at, "Track source first_seen_at")
        _aware(self.checked_at, "Track source checked_at")
        if self.checked_at.astimezone(UTC) < self.first_seen_at.astimezone(UTC):
            raise ValueError("Track source check cannot precede discovery.")


@dataclass(frozen=True, slots=True)
class Track:
    id: TrackId
    title: str
    duration_seconds: float | None
    artwork_url: str | None
    album_title: str | None
    release_date: date | None
    isrc: str | None
    created_at: datetime
    updated_at: datetime
    artists: tuple[ArtistCredit, ...] = ()
    sources: tuple[TrackSource, ...] = ()

    def __post_init__(self) -> None:
        _text(self.title, "Track title", maximum=500)
        _optional_text(self.artwork_url, "Artwork URL", maximum=2048)
        _optional_text(self.album_title, "Album title", maximum=500)
        _optional_text(self.isrc, "ISRC", maximum=15)
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds <= 0
        ):
            raise ValueError("Track duration must be positive and finite.")
        _aware(self.created_at, "Track created_at")
        _aware(self.updated_at, "Track updated_at")
        if self.updated_at.astimezone(UTC) < self.created_at.astimezone(UTC):
            raise ValueError("Track update cannot precede creation.")
        if type(self.artists) is not tuple or type(self.sources) is not tuple:
            raise ValueError("Track relations must be immutable tuples.")


@dataclass(frozen=True, slots=True)
class MediaReference:
    provider: ProviderName
    external_id: str
    kind: MediaKind
    source_url: str

    def __post_init__(self) -> None:
        _text(self.external_id, "Media external ID", maximum=200)
        _text(self.source_url, "Media source URL", maximum=2048)


@dataclass(frozen=True, slots=True)
class DiscoveryEntry:
    position: int
    track: Track
    source: TrackSource

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 0:
            raise ValueError("Discovery position must be non-negative.")
        if self.source.track_id != self.track.id:
            raise ValueError("Discovery source must belong to its track.")


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    id: DiscoverySnapshotId
    kind: DiscoveryKind
    provider_key: str
    locator: str
    limit: int
    fetched_at: datetime
    expires_at: datetime
    entries: tuple[DiscoveryEntry, ...]
    source_url: str | None = None
    playlist_title: str | None = None
    source_has_more: bool = False
    continuation: str | None = None

    def __post_init__(self) -> None:
        _text(self.provider_key, "Provider key", maximum=64)
        _text(self.locator, "Discovery locator", maximum=500)
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ValueError("Discovery limit must be between 1 and 100.")
        _aware(self.fetched_at, "Snapshot fetched_at")
        _aware(self.expires_at, "Snapshot expires_at")
        if self.expires_at.astimezone(UTC) <= self.fetched_at.astimezone(UTC):
            raise ValueError("Snapshot expiry must follow its fetch time.")
        if type(self.entries) is not tuple:
            raise ValueError("Discovery entries must be an immutable tuple.")
        if tuple(entry.position for entry in self.entries) != tuple(
            range(len(self.entries))
        ):
            raise ValueError("Discovery positions must be contiguous and ordered.")
        _optional_text(self.source_url, "Discovery source URL", maximum=2048)
        _optional_text(self.playlist_title, "Playlist title", maximum=500)
        _optional_text(self.continuation, "Provider continuation", maximum=4096)

    def is_fresh(self, now: datetime) -> bool:
        _aware(now, "Snapshot comparison time")
        return self.expires_at.astimezone(UTC) > now.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    snapshot: DiscoverySnapshot
    refreshing: bool
    stale: bool
