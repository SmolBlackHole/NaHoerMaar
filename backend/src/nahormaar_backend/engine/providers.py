# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Provider capabilities used by the catalog, never by queue persistence.

Adapters translate external payloads and clean up their own requests on
cancellation. The composition root owns their lifetime. The catalog owns routing,
metadata persistence, cache versions and request deduplication.
"""

from typing import Protocol, runtime_checkable

from .audio import PlayableSource
from .domain.catalog import (
    MediaKind,
    MediaReference,
    PlaylistPage,
    TrackFinding,
    TrackPage,
)
from .domain.tracks import MediaIdentity


class ProviderError(RuntimeError):
    """Adapter failure; the catalog/controller owns any bounded retry policy."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class UnsupportedCapability(ProviderError):
    """The selected provider does not support the requested operation."""


@runtime_checkable
class Provider(Protocol):
    @property
    def key(self) -> str:
        """Routing key, e.g. youtube_music; distinct from canonical media namespace."""
        ...

    def identify(
        self, source_url: str, *, kind: MediaKind | None = None
    ) -> MediaReference | None:
        """Parse without I/O. Prefer track on mixed links unless kind is specified."""
        ...

    async def close(self) -> None:
        """Cancel and settle owned work. Called by the composition root."""
        ...


@runtime_checkable
class TrackProvider(Provider, Protocol):
    async def track(self, identity: MediaIdentity) -> TrackFinding: ...


@runtime_checkable
class SearchProvider(Provider, Protocol):
    async def search(
        self, query: str, *, limit: int, continuation: str | None = None
    ) -> TrackPage: ...


@runtime_checkable
class PlaylistProvider(Provider, Protocol):
    async def playlist(
        self, identity: MediaIdentity, *, limit: int, continuation: str | None = None
    ) -> PlaylistPage: ...


@runtime_checkable
class RadioProvider(Provider, Protocol):
    async def radio_next(
        self, seed: MediaReference, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        """Return recommendations only. Capacity/exclusions belong to the strategy."""
        ...


@runtime_checkable
class PlaybackProvider(Provider, Protocol):
    async def resolve_audio(self, identity: MediaIdentity) -> PlayableSource: ...
