# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Normalized provider observations, before catalog persistence assigns IDs."""

from dataclasses import dataclass, field
from enum import StrEnum

from .metadata import TrackMetadata
from .tracks import ArtistIdentity, MediaIdentity, Track


class MediaKind(StrEnum):
    TRACK = "track"
    PLAYLIST = "playlist"


@dataclass(frozen=True, slots=True)
class MediaReference:
    identity: MediaIdentity
    kind: MediaKind
    source_url: str

    def __post_init__(self) -> None:
        if type(self.kind) is not MediaKind:
            raise ValueError("A media reference needs a MediaKind.")
        if not self.source_url or self.source_url != self.source_url.strip():
            raise ValueError("source_url must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class ArtistCredit:
    identity: ArtistIdentity
    name: str

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("Artist name must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class TrackFinding:
    """An observation, not a stored Track or a queue occurrence.

    Providers emit structured credits only for known external artist identities.
    None means credits were not supplied; an empty tuple is an explicit empty
    observation. The catalog, not a provider, resolves internal artist/track IDs.
    """

    reference: MediaReference
    metadata: TrackMetadata = field(default_factory=TrackMetadata)
    artists: tuple[ArtistCredit, ...] | None = None

    def __post_init__(self) -> None:
        if self.reference.kind is not MediaKind.TRACK:
            raise ValueError("A track finding needs a track reference.")
        if self.artists is not None:
            if type(self.artists) is not tuple:
                raise ValueError("Artist credits must be an immutable tuple.")
            if len({artist.identity for artist in self.artists}) != len(self.artists):
                raise ValueError("A finding cannot credit the same artist twice.")


@dataclass(frozen=True, slots=True)
class UnavailableFinding:
    """Keep an unavailable occurrence even when its source identity is unknown."""

    reason: str
    metadata: TrackMetadata = field(default_factory=TrackMetadata)
    reference: MediaReference | None = None

    def __post_init__(self) -> None:
        if not self.reason or self.reason != self.reason.strip():
            raise ValueError("An unavailable finding needs a reason.")
        if self.reference is not None and self.reference.kind is not MediaKind.TRACK:
            raise ValueError("An unavailable finding needs a track reference.")


@dataclass(frozen=True, slots=True)
class TrackPage:
    """Source order, including duplicates, with an opaque provider continuation.

    None ends pagination. The token belongs to the same provider and request;
    it is neither a public snapshot ID nor an internal queue position. An error
    may accompany partial results, which must not replace a complete cache entry.
    """

    entries: tuple[TrackFinding | UnavailableFinding, ...]
    continuation: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple:
            raise ValueError("Page entries must be an immutable tuple.")
        if self.continuation == "":
            raise ValueError("Use None for an exhausted continuation.")
        if self.error is not None and (
            not self.error or self.error != self.error.strip()
        ):
            raise ValueError("A partial page error must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class PlaylistPage:
    reference: MediaReference
    title: str | None
    page: TrackPage

    def __post_init__(self) -> None:
        if self.reference.kind is not MediaKind.PLAYLIST:
            raise ValueError("A playlist page needs a playlist reference.")
        if self.title is not None and (
            not self.title or self.title != self.title.strip()
        ):
            raise ValueError("Playlist title must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class Recommendations:
    """Stored track identities for queue filling, without unavailable source slots."""

    tracks: tuple[Track, ...]
    continuation: str | None = None
    error: str | None = None
