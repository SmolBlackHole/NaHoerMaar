# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Catalog ingestion, routing and persistent stale-while-revalidate discovery."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.observability import error_code, safe_log_value

from .domain import (
    DiscoveryKind,
    DiscoveryResult,
    DiscoverySnapshot,
    DiscoverySnapshotId,
    MediaKind,
    MediaReference,
    SourceAvailability,
    Track,
    TrackId,
    TrackSource,
    TrackSourceId,
)
from .providers import (
    CatalogProvider,
    ProviderAudio,
    ProviderError,
    ProviderPage,
    ProviderPlaylist,
    ProviderTrack,
)
from .repository import CatalogRepository, DiscoveryRepository

_LOGGER = logging.getLogger(__name__)
type UnitOfWorkFactory = Callable[[], UnitOfWork]
type RefreshKey = tuple[DiscoveryKind, str, str, int]
type RefreshLoader = Callable[[], Awaitable[ProviderPage | ProviderPlaylist]]


class CatalogErrorCode(StrEnum):
    INVALID_QUERY = "invalid_query"
    INVALID_LIMIT = "invalid_limit"
    UNKNOWN_PROVIDER = "unknown_provider"
    UNSUPPORTED_LINK = "unsupported_link"
    PROVIDER_FAILED = "provider_failed"
    CATALOG_CLOSED = "catalog_closed"
    INVALID_RADIO_SEED = "invalid_radio_seed"
    TRACK_NOT_FOUND = "track_not_found"
    AUDIO_SOURCE_NOT_FOUND = "audio_source_not_found"
    SNAPSHOT_NOT_FOUND = "snapshot_not_found"


class CatalogError(RuntimeError):
    def __init__(
        self,
        code: CatalogErrorCode,
        status: int = 400,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ResolvedAudio:
    track: Track
    source: TrackSource
    stream_url: str
    headers: tuple[tuple[str, str], ...] = ()
    is_opus: bool = False


@dataclass(frozen=True, slots=True)
class RadioPage:
    entries: tuple[TrackSource, ...]
    continuation: str | None = None


class CatalogService:
    """Own provider routing and shared refresh work, not provider lifetime data."""

    __slots__ = (
        "_clock",
        "_closed",
        "_playlist_ttl",
        "_providers",
        "_refreshes",
        "_search_ttl",
        "_units",
    )

    def __init__(
        self,
        units: UnitOfWorkFactory,
        providers: tuple[CatalogProvider, ...],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        search_ttl: timedelta = timedelta(minutes=5),
        playlist_ttl: timedelta = timedelta(minutes=1),
    ) -> None:
        if len({provider.key for provider in providers}) != len(providers):
            raise ValueError("Catalog provider keys must be unique.")
        if search_ttl <= timedelta(0) or playlist_ttl <= timedelta(0):
            raise ValueError("Catalog cache lifetimes must be positive.")
        self._units = units
        self._providers = {provider.key: provider for provider in providers}
        self._clock = clock
        self._search_ttl = search_ttl
        self._playlist_ttl = playlist_ttl
        self._refreshes: dict[RefreshKey, asyncio.Task[DiscoverySnapshot]] = {}
        self._closed = False

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        provider_key: str = "youtube_music",
        refresh: bool = False,
    ) -> DiscoveryResult:
        self._ensure_open()
        normalized = " ".join(query.split())
        if not 1 <= len(normalized) <= 200:
            raise CatalogError(CatalogErrorCode.INVALID_QUERY, 422)
        self._validate_limit(limit)
        provider = self._provider(provider_key)
        _LOGGER.info(
            "catalog.search_requested provider=%s query=%r limit=%d refresh=%s",
            provider.key,
            safe_log_value(normalized),
            limit,
            refresh,
        )
        key = (DiscoveryKind.SEARCH, provider.key, normalized.casefold(), limit)
        cached = await self._latest(key)
        if cached is None:
            _LOGGER.debug("catalog.cache_miss kind=search provider=%s", provider.key)
            return DiscoveryResult(
                await self._refresh(
                    key, lambda: self._search_page(provider, normalized, limit)
                ),
                False,
                False,
            )
        refreshing = refresh or not cached.is_fresh(self._clock())
        _LOGGER.debug(
            "catalog.cache_hit kind=search provider=%s snapshot_id=%s fresh=%s refresh=%s",
            provider.key,
            cached.id,
            cached.is_fresh(self._clock()),
            refreshing,
        )
        if refreshing:
            self._schedule(key, lambda: self._search_page(provider, normalized, limit))
        return DiscoveryResult(cached, refreshing, not cached.is_fresh(self._clock()))

    async def playlist(
        self,
        source_url: str,
        *,
        limit: int = 100,
        provider_key: str | None = None,
        refresh: bool = False,
    ) -> DiscoveryResult:
        self._ensure_open()
        self._validate_limit(limit)
        provider, reference = self._route(source_url, provider_key, MediaKind.PLAYLIST)
        _LOGGER.info(
            "catalog.playlist_requested provider=%s external_id=%s limit=%d refresh=%s",
            provider.key,
            safe_log_value(reference.external_id),
            limit,
            refresh,
        )
        key = (DiscoveryKind.PLAYLIST, provider.key, reference.external_id, limit)
        cached = await self._latest(key)
        if cached is None:
            _LOGGER.debug("catalog.cache_miss kind=playlist provider=%s", provider.key)
            return DiscoveryResult(
                await self._refresh(
                    key, lambda: self._playlist_page(provider, reference, limit)
                ),
                False,
                False,
            )
        refreshing = refresh or not cached.is_fresh(self._clock())
        _LOGGER.debug(
            "catalog.cache_hit kind=playlist provider=%s snapshot_id=%s fresh=%s "
            "refresh=%s",
            provider.key,
            cached.id,
            cached.is_fresh(self._clock()),
            refreshing,
        )
        if refreshing:
            self._schedule(key, lambda: self._playlist_page(provider, reference, limit))
        return DiscoveryResult(cached, refreshing, not cached.is_fresh(self._clock()))

    async def track(
        self,
        source_url: str,
        *,
        provider_key: str | None = None,
    ) -> Track:
        self._ensure_open()
        provider, reference = self._route(source_url, provider_key, MediaKind.TRACK)
        _LOGGER.info(
            "catalog.track_requested provider=%s external_id=%s",
            provider.key,
            safe_log_value(reference.external_id),
        )
        try:
            observation = await provider.track(reference)
        except ProviderError as error:
            raise CatalogError(
                CatalogErrorCode.PROVIDER_FAILED,
                502,
                retryable=error.retryable,
            ) from error
        if (
            observation.provider != reference.provider
            or observation.external_id != reference.external_id
        ):
            raise CatalogError(CatalogErrorCode.PROVIDER_FAILED, 502)
        observed_at = self._clock()
        async with self._units() as work:
            track = await CatalogRepository(work.session).upsert(
                observation, observed_at
            )
            await work.commit()
        _LOGGER.info(
            "catalog.track_saved track_id=%s provider=%s external_id=%s",
            track.id,
            provider.key,
            safe_log_value(reference.external_id),
        )
        return track

    async def tracks(self, track_ids: set[TrackId]) -> dict[TrackId, Track]:
        self._ensure_open()
        async with self._units() as work:
            return await CatalogRepository(work.session).tracks(track_ids)

    async def snapshot(
        self,
        snapshot_id: DiscoverySnapshotId,
        kind: DiscoveryKind,
    ) -> DiscoveryResult:
        """Load one immutable discovery version without provider I/O."""
        self._ensure_open()
        async with self._units() as work:
            snapshot = await DiscoveryRepository(work.session).get(snapshot_id)
        if snapshot is None or snapshot.kind is not kind:
            raise CatalogError(CatalogErrorCode.SNAPSHOT_NOT_FOUND, 404)
        key = (
            snapshot.kind,
            snapshot.provider_key,
            snapshot.locator,
            snapshot.limit,
        )
        return DiscoveryResult(
            snapshot,
            key in self._refreshes,
            not snapshot.is_fresh(self._clock()),
        )

    async def resolve_audio(
        self,
        track_id: TrackId,
        source_id: TrackSourceId | None = None,
    ) -> ResolvedAudio:
        """Resolve a short-lived provider stream for one canonical track."""
        self._ensure_open()
        async with self._units() as work:
            catalog = CatalogRepository(work.session)
            tracks = await catalog.tracks({track_id})
            track = tracks.get(track_id)
            if track is None:
                raise CatalogError(CatalogErrorCode.TRACK_NOT_FOUND, 404)
            if source_id is not None:
                source = await catalog.source(source_id)
                if source is None or source.track_id != track_id:
                    raise CatalogError(CatalogErrorCode.AUDIO_SOURCE_NOT_FOUND, 404)
            else:
                source = next(
                    (
                        candidate
                        for candidate in track.sources
                        if candidate.availability is not SourceAvailability.UNAVAILABLE
                    ),
                    None,
                )
                if source is None:
                    raise CatalogError(CatalogErrorCode.AUDIO_SOURCE_NOT_FOUND, 404)

        provider, reference = self._route(source.source_url, None, MediaKind.TRACK)
        try:
            audio: ProviderAudio = await provider.resolve_audio(reference)
        except ProviderError as error:
            raise CatalogError(
                CatalogErrorCode.PROVIDER_FAILED,
                502,
                retryable=error.retryable,
            ) from error
        _LOGGER.info(
            "catalog.audio_resolved track_id=%s source_id=%s provider=%s opus=%s",
            track.id,
            source.id,
            provider.key,
            audio.is_opus,
        )
        return ResolvedAudio(
            track,
            source,
            audio.stream_url,
            audio.headers,
            audio.is_opus,
        )

    async def radio(
        self,
        *,
        source_id: TrackSourceId | None = None,
        snapshot_id: DiscoverySnapshotId | None = None,
        limit: int = 20,
        continuation: str | None = None,
    ) -> RadioPage:
        self._ensure_open()
        self._validate_limit(limit)
        if (source_id is None) == (snapshot_id is None):
            raise CatalogError(CatalogErrorCode.INVALID_RADIO_SEED, 422)

        async with self._units() as work:
            catalog = CatalogRepository(work.session)
            if source_id is not None:
                source = await catalog.source(source_id)
                if source is None:
                    raise CatalogError(CatalogErrorCode.INVALID_RADIO_SEED, 404)
                source_url = source.source_url
                kind = MediaKind.TRACK
            else:
                if snapshot_id is None:
                    raise AssertionError("Validated radio snapshot is missing.")
                snapshot = await DiscoveryRepository(work.session).get(snapshot_id)
                if (
                    snapshot is None
                    or snapshot.kind is not DiscoveryKind.PLAYLIST
                    or snapshot.source_url is None
                ):
                    raise CatalogError(CatalogErrorCode.INVALID_RADIO_SEED, 404)
                source_url = snapshot.source_url
                kind = MediaKind.PLAYLIST

        provider, reference = self._route(source_url, None, kind)
        _LOGGER.info(
            "catalog.radio_requested provider=%s kind=%s external_id=%s limit=%d continuation=%s",
            provider.key,
            kind.value,
            safe_log_value(reference.external_id),
            limit,
            continuation is not None,
        )
        try:
            page = await provider.radio(
                reference,
                limit=limit,
                continuation=continuation,
            )
        except ProviderError as error:
            raise CatalogError(
                CatalogErrorCode.PROVIDER_FAILED,
                502,
                retryable=error.retryable,
            ) from error

        observed_at = self._clock()
        async with self._units() as work:
            catalog = CatalogRepository(work.session)
            sources: list[TrackSource] = []
            for observation in page.entries:
                track = await catalog.upsert(observation, observed_at)
                source_identity = _source_id(track, observation)
                source = next(
                    item for item in track.sources if item.id == source_identity
                )
                sources.append(source)
            await work.commit()
        result = RadioPage(tuple(sources), page.continuation)
        _LOGGER.info(
            "catalog.radio_completed provider=%s kind=%s entries=%d continuation=%s",
            provider.key,
            kind.value,
            len(result.entries),
            result.continuation is not None,
        )
        return result

    async def prune_orphans(self, checked_before: datetime) -> tuple[int, int, int]:
        self._ensure_open()
        async with self._units() as work:
            removed = await CatalogRepository(work.session).prune_orphans(
                checked_before
            )
            await work.commit()
        _LOGGER.info(
            "catalog.orphans_pruned tracks=%d artists=%d sources=%d",
            removed[0],
            removed[1],
            removed[2],
        )
        return removed

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._refreshes.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(provider.close() for provider in self._providers.values())
        )
        _LOGGER.info("catalog.closed providers=%s", ",".join(self._providers))

    async def _latest(self, key: RefreshKey) -> DiscoverySnapshot | None:
        async with self._units() as work:
            return await DiscoveryRepository(work.session).latest(*key)

    def _schedule(
        self,
        key: RefreshKey,
        load: RefreshLoader,
    ) -> None:
        if key in self._refreshes:
            _LOGGER.debug(
                "catalog.refresh_deduplicated kind=%s provider=%s", key[0].value, key[1]
            )
            return
        _LOGGER.debug(
            "catalog.refresh_scheduled kind=%s provider=%s limit=%d",
            key[0].value,
            key[1],
            key[3],
        )
        task = asyncio.create_task(self._load_and_publish(key, load))
        self._refreshes[key] = task
        task.add_done_callback(lambda completed: self._finished(key, completed))

    async def _refresh(
        self,
        key: RefreshKey,
        load: RefreshLoader,
    ) -> DiscoverySnapshot:
        self._schedule(key, load)
        return await asyncio.shield(self._refreshes[key])

    def _finished(self, key: RefreshKey, task: asyncio.Task[DiscoverySnapshot]) -> None:
        if self._refreshes.get(key) is task:
            self._refreshes.pop(key, None)
        if task.cancelled():
            _LOGGER.debug(
                "catalog.refresh_cancelled kind=%s provider=%s", key[0].value, key[1]
            )
            return
        error = task.exception()
        if error is not None:
            _LOGGER.warning(
                "catalog.refresh_failed kind=%s provider=%s error_code=%s retryable=%s",
                key[0].value,
                key[1],
                error_code(error),
                getattr(error, "retryable", False),
            )

    async def _load_and_publish(
        self,
        key: RefreshKey,
        load: RefreshLoader,
    ) -> DiscoverySnapshot:
        try:
            loaded = await load()
        except ProviderError as error:
            raise CatalogError(
                CatalogErrorCode.PROVIDER_FAILED,
                502,
                retryable=error.retryable,
            ) from error
        fetched_at = self._clock()
        kind, provider_key, locator, limit = key
        if kind is DiscoveryKind.SEARCH:
            if not isinstance(loaded, ProviderPage):
                raise RuntimeError("Search refresh returned an invalid provider page.")
            page = loaded
            source_url = None
            playlist_title = None
            expires_at = fetched_at + self._search_ttl
        else:
            if not isinstance(loaded, ProviderPlaylist):
                raise RuntimeError(
                    "Playlist refresh returned an invalid provider page."
                )
            page = loaded.page
            source_url = loaded.reference.source_url
            playlist_title = loaded.title
            expires_at = fetched_at + self._playlist_ttl
        async with self._units() as work:
            catalog = CatalogRepository(work.session)
            source_ids: list[TrackSourceId] = []
            for observation in page.entries:
                track = await catalog.upsert(observation, fetched_at)
                source_ids.append(_source_id(track, observation))
            snapshot = await DiscoveryRepository(work.session).publish(
                kind=kind,
                provider_key=provider_key,
                locator=locator,
                limit=limit,
                source_url=source_url,
                playlist_title=playlist_title,
                source_ids=tuple(source_ids),
                fetched_at=fetched_at,
                expires_at=expires_at,
                source_has_more=page.continuation is not None,
                continuation=page.continuation,
            )
            await work.commit()
        _LOGGER.info(
            "catalog.refresh_completed kind=%s provider=%s entries=%s snapshot_id=%s",
            kind.value,
            provider_key,
            len(snapshot.entries),
            snapshot.id,
        )
        return snapshot

    async def _search_page(
        self, provider: CatalogProvider, query: str, limit: int
    ) -> ProviderPage:
        return await provider.search(query, limit=limit)

    async def _playlist_page(
        self,
        provider: CatalogProvider,
        reference: MediaReference,
        limit: int,
    ) -> ProviderPlaylist:
        result = await provider.playlist(reference, limit=limit)
        if result.reference != reference:
            raise ProviderError("Provider returned another playlist identity.")
        return result

    def _route(
        self,
        source_url: str,
        provider_key: str | None,
        kind: MediaKind,
    ) -> tuple[CatalogProvider, MediaReference]:
        providers = (
            (self._provider(provider_key),)
            if provider_key is not None
            else tuple(self._providers.values())
        )
        for provider in providers:
            reference = provider.identify(source_url, kind=kind)
            if reference is not None:
                return provider, reference
        raise CatalogError(CatalogErrorCode.UNSUPPORTED_LINK, 422)

    def _provider(self, provider_key: str) -> CatalogProvider:
        provider = self._providers.get(provider_key)
        if provider is None:
            raise CatalogError(CatalogErrorCode.UNKNOWN_PROVIDER, 422)
        return provider

    def _ensure_open(self) -> None:
        if self._closed:
            raise CatalogError(CatalogErrorCode.CATALOG_CLOSED, 503)

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise CatalogError(CatalogErrorCode.INVALID_LIMIT, 422)


def _source_id(track: Track, observation: ProviderTrack) -> TrackSourceId:
    for source in track.sources:
        if (
            source.provider == observation.provider
            and source.external_id == observation.external_id
        ):
            return source.id
    raise RuntimeError("Persisted track is missing its observed source.")
