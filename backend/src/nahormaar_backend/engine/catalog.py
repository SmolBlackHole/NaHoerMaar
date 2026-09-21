# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Route catalog work and persist observations after provider I/O finishes."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from time import monotonic
from uuid import UUID

from .audio import PlayableSource
from .cache import RefreshStatus, Snapshot, SnapshotCache
from .domain.catalog import (
    MediaKind,
    MediaReference,
    PlaylistPage,
    Recommendations,
    TrackFinding,
    TrackPage,
)
from .domain.metadata import MetadataKind, MetadataSource
from .domain.tracks import MediaIdentity, Track
from .metadata import MetadataStore
from .providers import (
    PlaybackProvider,
    PlaylistProvider,
    Provider,
    ProviderError,
    RadioProvider,
    SearchProvider,
    TrackProvider,
    UnsupportedCapability,
)


class Catalog:
    """Borrow providers in routing-priority order and one shared metadata store.

    The composition root owns provider lifetime; the catalog owns cache refresh
    tasks. An explicit key never silently falls
    back to another provider when a link or capability is unsupported.
    """

    def __init__(
        self,
        providers: tuple[Provider, ...],
        metadata: MetadataStore,
        *,
        clock: Callable[[], datetime],
        timer: Callable[[], float] = monotonic,
    ) -> None:
        if len({provider.key for provider in providers}) != len(providers):
            raise ValueError("Provider routing keys must be unique.")
        self._providers = {provider.key: provider for provider in providers}
        self._metadata = metadata
        self._clock = clock
        self._searches = SnapshotCache[tuple[str, str, int], TrackPage](
            ttl=300, clock=timer, error=lambda page: page.error
        )
        self._playlists = SnapshotCache[tuple[str, MediaIdentity, int], PlaylistPage](
            ttl=60, clock=timer, error=lambda playlist: playlist.page.error
        )
        self._tracks = SnapshotCache[
            tuple[str, MediaIdentity], tuple[TrackFinding, MetadataSource]
        ](ttl=300, clock=timer)
        self._closed = False

    def _route(
        self,
        source_url: str,
        provider_key: str | None,
        *,
        kind: MediaKind = MediaKind.TRACK,
    ) -> tuple[Provider, MediaReference]:
        if self._closed:
            raise RuntimeError("Catalog is closed.")
        if provider_key is not None and provider_key not in self._providers:
            raise UnsupportedCapability("Unknown media provider.")
        providers = (
            (self._providers[provider_key],)
            if provider_key is not None
            else self._providers.values()
        )
        for provider in providers:
            reference = (
                provider.identify(source_url, kind=MediaKind.PLAYLIST)
                if kind is MediaKind.PLAYLIST
                else provider.identify(source_url)
            )
            if reference is not None:
                if reference.kind is not kind:
                    required = "single track" if kind is MediaKind.TRACK else "playlist"
                    raise UnsupportedCapability(
                        f"This operation requires a {required}."
                    )
                return provider, reference
        raise UnsupportedCapability("This media link is not supported.")

    async def search(
        self,
        query: str,
        *,
        limit: int = 100,
        provider_key: str = "youtube_music",
        refresh: bool = False,
    ) -> Snapshot[TrackPage]:
        if self._closed:
            raise RuntimeError("Catalog is closed.")
        query = " ".join(query.split())
        if (
            not 1 <= len(query) <= 200
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError(
                "Search needs 1 to 200 characters and a result limit of 1 to 100."
            )
        provider = self._providers.get(provider_key)
        if provider is None:
            raise UnsupportedCapability("Unknown media provider.")
        if not isinstance(provider, SearchProvider):
            raise UnsupportedCapability("This provider cannot search for tracks.")

        async def load() -> TrackPage:
            source = MetadataSource(provider.key, MetadataKind.DISCOVERY, self._clock())
            page = await provider.search(query, limit=limit)
            await self._metadata.remember(
                tuple(
                    entry for entry in page.entries if isinstance(entry, TrackFinding)
                ),
                source=source,
            )
            return page

        return await self._searches.get(
            (provider.key, query, limit), load, refresh=refresh
        )

    async def playlist(
        self,
        source_url: str,
        *,
        limit: int = 100,
        provider_key: str | None = None,
        refresh: bool = False,
    ) -> Snapshot[PlaylistPage]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Playlist result limit must be between 1 and 100.")
        provider, reference = self._route(
            source_url, provider_key, kind=MediaKind.PLAYLIST
        )
        if not isinstance(provider, PlaylistProvider):
            raise UnsupportedCapability("This provider cannot retrieve playlists.")

        async def load() -> PlaylistPage:
            source = MetadataSource(provider.key, MetadataKind.DISCOVERY, self._clock())
            playlist = await provider.playlist(reference.identity, limit=limit)
            if playlist.reference.identity != reference.identity:
                raise ProviderError(
                    "The provider returned a different playlist identity."
                )
            await self._metadata.remember(
                tuple(
                    entry
                    for entry in playlist.page.entries
                    if isinstance(entry, TrackFinding)
                ),
                source=source,
            )
            return playlist

        return await self._playlists.get(
            (provider.key, reference.identity, limit), load, refresh=refresh
        )

    async def track(self, source_url: str, *, provider_key: str | None = None) -> Track:
        provider, reference = self._route(source_url, provider_key)
        if not isinstance(provider, TrackProvider):
            raise UnsupportedCapability("This provider cannot retrieve track details.")

        async def load() -> tuple[TrackFinding, MetadataSource]:
            source = MetadataSource(provider.key, MetadataKind.DETAIL, self._clock())
            finding = await provider.track(reference.identity)
            if finding.reference.identity != reference.identity:
                raise ProviderError("The provider returned a different track identity.")
            return finding, source

        finding, source = (
            await self._tracks.get((provider.key, reference.identity), load)
        ).value
        (track,) = await self._metadata.remember((finding,), source=source)
        return track

    async def radio_next(
        self,
        seed: MediaReference,
        *,
        limit: int = 100,
        provider_key: str = "youtube_music",
        continuation: str | None = None,
    ) -> Recommendations:
        provider, reference = self._route(seed.source_url, provider_key, kind=seed.kind)
        if reference.identity != seed.identity:
            raise ValueError("Radio seed identity does not match its source.")
        if not isinstance(provider, RadioProvider):
            raise UnsupportedCapability(
                "This provider cannot discover radio recommendations."
            )
        source = MetadataSource(provider.key, MetadataKind.DISCOVERY, self._clock())
        page = await provider.radio_next(
            reference, limit=limit, continuation=continuation
        )
        tracks = await self._metadata.remember(
            tuple(entry for entry in page.entries if isinstance(entry, TrackFinding)),
            source=source,
        )
        return Recommendations(tracks, page.continuation, page.error)

    def search_snapshot(self, version: UUID) -> Snapshot[TrackPage]:
        return self._searches.read(version)

    def playlist_snapshot(self, version: UUID) -> Snapshot[PlaylistPage]:
        return self._playlists.read(version)

    def refresh_status(self, version: UUID, *, playlist: bool = False) -> RefreshStatus:
        return (
            self._playlists.status(version)
            if playlist
            else self._searches.status(version)
        )

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(
            self._searches.close(), self._playlists.close(), self._tracks.close()
        )

    async def resolve_audio(
        self, source_url: str | UUID, *, provider_key: str | None = None
    ) -> PlayableSource:
        if isinstance(source_url, UUID):
            source_url = (await self._metadata.get(source_url)).source_url
        provider, reference = self._route(source_url, provider_key)
        if not isinstance(provider, PlaybackProvider):
            raise UnsupportedCapability("This provider cannot resolve audio.")
        source = MetadataSource(provider.key, MetadataKind.DETAIL, self._clock())
        playable = await provider.resolve_audio(reference.identity)
        if playable.track.reference.identity != reference.identity:
            raise ProviderError("The provider returned a different track identity.")
        await self._metadata.remember((playable.track,), source=source)
        return playable
