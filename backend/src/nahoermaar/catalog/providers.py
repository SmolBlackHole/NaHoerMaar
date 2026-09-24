# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Provider observations at the catalog integration boundary."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from .domain import (
    MediaKind,
    MediaReference,
    ObservationQuality,
    ProviderName,
    SourceAvailability,
)


class ProviderError(RuntimeError):
    """A provider request failed without changing persisted catalog state."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ProviderArtist:
    provider: ProviderName
    external_id: str
    name: str

    def __post_init__(self) -> None:
        if not self.external_id or self.external_id != self.external_id.strip():
            raise ValueError("Artist external ID must be non-empty and trimmed.")
        if not self.name or self.name != self.name.strip():
            raise ValueError("Artist name must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class ProviderTrack:
    provider: ProviderName
    external_id: str
    source_url: str
    title: str
    artist_text: str | None = None
    artists: tuple[ProviderArtist, ...] = ()
    duration_seconds: float | None = None
    artwork_url: str | None = None
    uploader_name: str | None = None
    uploader_url: str | None = None
    album_title: str | None = None
    release_date: date | None = None
    isrc: str | None = None
    quality: ObservationQuality = ObservationQuality.DISCOVERY
    availability: SourceAvailability = SourceAvailability.AVAILABLE

    def __post_init__(self) -> None:
        if not self.external_id or self.external_id != self.external_id.strip():
            raise ValueError("Track external ID must be non-empty and trimmed.")
        if not self.source_url or self.source_url != self.source_url.strip():
            raise ValueError("Track source URL must be non-empty and trimmed.")
        if not self.title or self.title != self.title.strip():
            raise ValueError("Track title must be non-empty and trimmed.")
        if type(self.artists) is not tuple:
            raise ValueError("Provider artists must be an immutable tuple.")
        identities = {(artist.provider, artist.external_id) for artist in self.artists}
        if len(identities) != len(self.artists):
            raise ValueError("Provider artist identities must be unique per track.")


@dataclass(frozen=True, slots=True)
class ProviderPage:
    entries: tuple[ProviderTrack, ...]
    continuation: str | None = None

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple:
            raise ValueError("Provider entries must be an immutable tuple.")


@dataclass(frozen=True, slots=True)
class ProviderPlaylist:
    reference: MediaReference
    title: str | None
    page: ProviderPage

    def __post_init__(self) -> None:
        if self.reference.kind is not MediaKind.PLAYLIST:
            raise ValueError("A provider playlist needs a playlist reference.")


@runtime_checkable
class CatalogProvider(Protocol):
    @property
    def key(self) -> str: ...

    def identify(
        self,
        source_url: str,
        *,
        kind: MediaKind | None = None,
    ) -> MediaReference | None: ...

    async def search(self, query: str, *, limit: int) -> ProviderPage: ...

    async def playlist(
        self,
        reference: MediaReference,
        *,
        limit: int,
    ) -> ProviderPlaylist: ...

    async def track(self, reference: MediaReference) -> ProviderTrack: ...

    async def close(self) -> None: ...
