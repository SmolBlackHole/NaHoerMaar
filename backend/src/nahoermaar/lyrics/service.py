# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Lyrics lookup and bounded persistent caching."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import json

from nahoermaar.catalog.domain import Track, TrackId
from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory

from .domain import LyricsResult, LyricsState, TrackLyrics
from .providers import LyricsProvider, LyricsProviderError
from .repository import LyricsRepository


class LyricsErrorCode(StrEnum):
    TRACK_NOT_FOUND = "lyrics_track_not_found"
    PROVIDER_UNAVAILABLE = "lyrics_provider_unavailable"


class LyricsError(RuntimeError):
    def __init__(
        self,
        code: LyricsErrorCode,
        status: int,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class LyricsService:
    __slots__ = (
        "_catalog",
        "_clock",
        "_loads",
        "_miss_ttl",
        "_provider",
        "_success_ttl",
        "_units",
    )

    def __init__(
        self,
        units: UnitOfWorkFactory,
        catalog: CatalogService,
        provider: LyricsProvider,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        success_ttl: timedelta = timedelta(days=30),
        miss_ttl: timedelta = timedelta(hours=12),
    ) -> None:
        if success_ttl <= timedelta(0) or miss_ttl <= timedelta(0):
            raise ValueError("Lyrics cache lifetimes must be positive.")
        self._units = units
        self._catalog = catalog
        self._provider = provider
        self._clock = clock
        self._success_ttl = success_ttl
        self._miss_ttl = miss_ttl
        self._loads: dict[TrackId, asyncio.Task[TrackLyrics]] = {}

    async def get(
        self,
        track_id: TrackId,
        *,
        refresh: bool = False,
    ) -> LyricsResult:
        track = (await self._catalog.tracks({track_id})).get(track_id)
        if track is None:
            raise LyricsError(LyricsErrorCode.TRACK_NOT_FOUND, 404)
        signature = _metadata_signature(track)
        now = self._clock()
        cached = await self._cached(track_id)
        if cached is not None and not refresh and cached.is_fresh(now, signature):
            return LyricsResult(cached, cached=True)
        try:
            loaded = await self._load(track, signature)
        except LyricsProviderError as error:
            if (
                cached is not None
                and cached.metadata_signature == signature
                and cached.state is not LyricsState.NOT_FOUND
            ):
                return LyricsResult(cached, cached=True, stale=True)
            raise LyricsError(
                LyricsErrorCode.PROVIDER_UNAVAILABLE,
                502,
                retryable=error.retryable,
                retry_after_seconds=error.retry_after_seconds,
            ) from error
        return LyricsResult(loaded, cached=False)

    async def _cached(self, track_id: TrackId) -> TrackLyrics | None:
        async with self._units() as work:
            return await LyricsRepository(work.session).get(track_id)

    async def _load(self, track: Track, signature: str) -> TrackLyrics:
        existing = self._loads.get(track.id)
        if existing is not None:
            return await asyncio.shield(existing)
        task = asyncio.create_task(
            self._fetch(track, signature),
            name=f"lyrics:{track.id}",
        )
        self._loads[track.id] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._loads.get(track.id) is task:
                self._loads.pop(track.id, None)

    async def _fetch(self, track: Track, signature: str) -> TrackLyrics:
        artist_name = ", ".join(
            credit.artist.name
            for credit in sorted(track.artists, key=lambda item: item.position)
        )
        fetched_at = self._clock()
        provider_result = (
            await self._provider.get(
                track_name=track.title,
                artist_name=artist_name,
                album_name=track.album_title,
                duration_seconds=track.duration_seconds,
            )
            if artist_name
            else None
        )
        if provider_result is None:
            lyrics = TrackLyrics(
                track.id,
                signature,
                LyricsState.NOT_FOUND,
                None,
                None,
                None,
                fetched_at,
                fetched_at + self._miss_ttl,
            )
        else:
            lyrics = TrackLyrics(
                track.id,
                signature,
                (
                    LyricsState.INSTRUMENTAL
                    if provider_result.instrumental
                    else LyricsState.AVAILABLE
                ),
                provider_result.record_id,
                provider_result.plain_lyrics,
                provider_result.synced_lyrics,
                fetched_at,
                fetched_at + self._success_ttl,
            )
        async with self._units() as work:
            await LyricsRepository(work.session).save(lyrics)
            await work.commit()
        return lyrics


def _metadata_signature(track: Track) -> str:
    payload = {
        "title": track.title.casefold(),
        "artists": [
            credit.artist.name.casefold()
            for credit in sorted(track.artists, key=lambda item: item.position)
        ],
        "album": track.album_title.casefold() if track.album_title else None,
        "duration": (
            round(track.duration_seconds)
            if track.duration_seconds is not None
            else None
        ),
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
