# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from nahoermaar.catalog.domain import (
    DiscoveryKind,
    MediaKind,
    MediaReference,
    ProviderName,
)
from nahoermaar.catalog.providers import (
    ProviderAudio,
    ProviderError,
    ProviderPage,
    ProviderPlaylist,
    ProviderTrack,
)
from nahoermaar.catalog.repository import DiscoveryRepository
from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
TRACK = ProviderTrack(
    ProviderName.YOUTUBE,
    "abcdefghijk",
    "https://www.youtube.com/watch?v=abcdefghijk",
    "First title",
)
PLAYLIST = MediaReference(
    ProviderName.YOUTUBE,
    "PLabcdefghijk",
    MediaKind.PLAYLIST,
    "https://www.youtube.com/playlist?list=PLabcdefghijk",
)


class Provider:
    key = "youtube_music"

    def __init__(self) -> None:
        self.search_calls = 0
        self.playlist_calls = 0
        self.radio_calls = 0
        self.title = "First title"
        self.fail = False
        self.started: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    def identify(
        self, source_url: str, *, kind: MediaKind | None = None
    ) -> MediaReference | None:
        if source_url == PLAYLIST.source_url and kind in {None, MediaKind.PLAYLIST}:
            return PLAYLIST
        if source_url == TRACK.source_url and kind in {None, MediaKind.TRACK}:
            return MediaReference(
                ProviderName.YOUTUBE,
                TRACK.external_id,
                MediaKind.TRACK,
                TRACK.source_url,
            )
        return None

    async def search(self, query: str, *, limit: int) -> ProviderPage:
        assert query == "Zara Larsson" and limit == 50
        self.search_calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise ProviderError("temporary", retryable=True)
        return ProviderPage(
            (
                ProviderTrack(
                    ProviderName.YOUTUBE,
                    TRACK.external_id,
                    TRACK.source_url,
                    self.title,
                ),
            )
        )

    async def playlist(
        self, reference: MediaReference, *, limit: int
    ) -> ProviderPlaylist:
        assert reference == PLAYLIST and limit == 100
        self.playlist_calls += 1
        return ProviderPlaylist(reference, "Playlist", ProviderPage((TRACK,)))

    async def track(self, reference: MediaReference) -> ProviderTrack:
        return TRACK

    async def radio(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        assert reference.kind in {MediaKind.TRACK, MediaKind.PLAYLIST}
        assert limit == 20
        assert continuation is None
        self.radio_calls += 1
        return ProviderPage((TRACK,))

    async def resolve_audio(self, reference: MediaReference) -> ProviderAudio:
        assert reference.external_id == TRACK.external_id
        return ProviderAudio(
            "https://audio.example.test/stream",
            (("User-Agent", "NaHoerMaar test"),),
            True,
        )

    async def close(self) -> None:
        return None


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def test_cache_first_refresh_is_shared_and_provider_failure_keeps_last_snapshot() -> (
    None
):
    database = _database()
    provider = Provider()
    now = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: now[0])

    async def scenario() -> None:
        first = await service.search("  Zara   Larsson  ")
        assert not first.stale and not first.refreshing
        assert provider.search_calls == 1
        cached = await service.search("Zara Larsson")
        assert cached.snapshot.id == first.snapshot.id
        assert provider.search_calls == 1

        now[0] += timedelta(minutes=6)
        provider.title = "Refreshed title"
        provider.started = asyncio.Event()
        provider.release = asyncio.Event()
        stale = await service.search("Zara Larsson")
        same = await service.search("Zara Larsson")
        assert stale.stale and stale.refreshing
        assert same.snapshot.id == first.snapshot.id
        await provider.started.wait()
        assert provider.search_calls == 2
        provider.release.set()
        provider.started = None
        provider.release = None

        versions = [first.snapshot.id]

        async def refreshed() -> bool:
            latest = await service.search("Zara Larsson")
            versions.append(latest.snapshot.id)
            return latest.snapshot.id != first.snapshot.id

        for _ in range(100):
            if await refreshed():
                break
            await asyncio.sleep(0.01)
        assert versions[-1] != first.snapshot.id
        latest = await service.search("Zara Larsson")
        assert latest.snapshot.entries[0].track.title == "Refreshed title"

        now[0] += timedelta(minutes=6)
        provider.fail = True
        failed_refresh = await service.search("Zara Larsson")
        assert failed_refresh.stale and failed_refresh.refreshing
        await asyncio.sleep(0.05)
        async with units() as work:
            preserved = await DiscoveryRepository(work.session).latest(
                DiscoveryKind.SEARCH,
                provider.key,
                "zara larsson",
                50,
            )
        assert preserved is not None
        assert preserved.id == latest.snapshot.id
        assert preserved.entries[0].track.title == "Refreshed title"

        provider.fail = False
        playlist = await service.playlist(PLAYLIST.source_url)
        assert playlist.snapshot.playlist_title == "Playlist"
        async with units() as work:
            source_count = await work.session.scalar(
                select(func.count()).select_from(Base.metadata.tables["track_sources"])
            )
        assert source_count == 1
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_radio_resolves_persisted_seed_and_returns_canonical_sources() -> None:
    database = _database()
    provider = Provider()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: NOW)

    async def scenario() -> None:
        track = await service.track(TRACK.source_url)
        page = await service.radio(source_id=track.sources[0].id)
        assert provider.radio_calls == 1
        assert page.entries[0].track_id == track.id
        assert page.entries[0].id == track.sources[0].id
        audio = await service.resolve_audio(track.id, track.sources[0].id)
        assert audio.track.id == track.id
        assert audio.source.id == track.sources[0].id
        assert audio.is_opus
        assert audio.headers == (("User-Agent", "NaHoerMaar test"),)
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())
