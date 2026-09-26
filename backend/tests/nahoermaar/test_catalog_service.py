# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import func, select

from nahoermaar.api.catalog import router as catalog_router
from nahoermaar.catalog.domain import (
    DiscoveryKind,
    MediaKind,
    MediaReference,
    ObservationQuality,
    ProviderName,
)
from nahoermaar.catalog.providers import (
    ProviderArtist,
    ProviderAudio,
    ProviderError,
    ProviderPage,
    ProviderPlaylist,
    ProviderTrack,
)
from nahoermaar.catalog.repository import DiscoveryRepository
from nahoermaar.catalog.service import CatalogError, CatalogErrorCode, CatalogService
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
        self.track_calls = 0
        self.title = "First title"
        self.detail = TRACK
        self.radio_entries: tuple[ProviderTrack, ...] = (TRACK,)
        self.extra_tracks: tuple[ProviderTrack, ...] = ()
        self.continued_tracks: tuple[ProviderTrack, ...] = ()
        self.search_continuation: str | None = None
        self.playlist_continuation: str | None = None
        self.fail = False
        self.started: asyncio.Event | None = None
        self.release: asyncio.Event | None = None
        self.track_started: asyncio.Event | None = None
        self.track_release: asyncio.Event | None = None

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

    async def search(
        self,
        query: str,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        assert (
            query
            in {
                "Zara Larsson",
                "zara larsson",
                "Paging",
                "paging",
                "5l33p",
            }
            and limit == 50
        )
        self.search_calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise ProviderError("temporary", retryable=True)
        if continuation is not None:
            assert continuation == self.search_continuation
            return ProviderPage(self.continued_tracks)
        return ProviderPage(
            (
                ProviderTrack(
                    ProviderName.YOUTUBE,
                    TRACK.external_id,
                    TRACK.source_url,
                    self.title,
                ),
                *self.extra_tracks,
            ),
            self.search_continuation,
        )

    async def playlist(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPlaylist:
        assert reference == PLAYLIST and limit == 100
        self.playlist_calls += 1
        if continuation is not None:
            assert continuation == self.playlist_continuation
            return ProviderPlaylist(
                reference, "Playlist", ProviderPage(self.continued_tracks)
            )
        return ProviderPlaylist(
            reference,
            "Playlist",
            ProviderPage((TRACK,), self.playlist_continuation),
        )

    async def track(self, reference: MediaReference) -> ProviderTrack:
        self.track_calls += 1
        if self.track_started is not None:
            self.track_started.set()
        if self.track_release is not None:
            await self.track_release.wait()
        return self.detail

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
        return ProviderPage(self.radio_entries)

    async def resolve_audio(self, reference: MediaReference) -> ProviderAudio:
        assert reference.external_id == TRACK.external_id
        return ProviderAudio(
            "https://audio.example.test/stream",
            (("User-Agent", "NaHoerMaar test"),),
            True,
        )

    async def close(self) -> None:
        return None


class YouTubeProvider(Provider):
    key = "youtube"


class ParallelYouTubeProvider(YouTubeProvider):
    def __init__(self, expected_parallel: int) -> None:
        super().__init__()
        self.expected_parallel = expected_parallel
        self.active_tracks = 0
        self.max_active_tracks = 0
        self.parallel_started = asyncio.Event()
        self.parallel_release = asyncio.Event()

    def identify(
        self, source_url: str, *, kind: MediaKind | None = None
    ) -> MediaReference | None:
        prefix = "https://www.youtube.com/watch?v="
        if source_url.startswith(prefix) and kind in {None, MediaKind.TRACK}:
            return MediaReference(
                ProviderName.YOUTUBE,
                source_url.removeprefix(prefix),
                MediaKind.TRACK,
                source_url,
            )
        return super().identify(source_url, kind=kind)

    async def track(self, reference: MediaReference) -> ProviderTrack:
        self.track_calls += 1
        self.active_tracks += 1
        self.max_active_tracks = max(self.max_active_tracks, self.active_tracks)
        if self.active_tracks == self.expected_parallel:
            self.parallel_started.set()
        try:
            await self.parallel_release.wait()
        finally:
            self.active_tracks -= 1
        return ProviderTrack(
            ProviderName.YOUTUBE,
            reference.external_id,
            reference.source_url,
            f"Track {reference.external_id}",
            artist_text="Known artist",
            duration_seconds=181,
            quality=ObservationQuality.DETAIL,
        )


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

        reopened = await service.snapshot(latest.snapshot.id, DiscoveryKind.SEARCH)
        assert reopened.snapshot == latest.snapshot
        assert not reopened.stale
        with pytest.raises(CatalogError) as mismatch:
            await service.snapshot(latest.snapshot.id, DiscoveryKind.PLAYLIST)
        assert mismatch.value.code is CatalogErrorCode.SNAPSHOT_NOT_FOUND

        provider.extra_tracks = (
            ProviderTrack(
                ProviderName.YOUTUBE,
                "secondtrack",
                "https://www.youtube.com/watch?v=secondtrack",
                "Second title",
            ),
        )
        paged = await service.search("Paging")
        api = FastAPI()
        api.include_router(catalog_router(service))
        transport = httpx.ASGITransport(app=api)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:3000",
        ) as client:
            response = await client.get(
                f"/api/catalog/search/{paged.snapshot.id}",
                params={"offset": 1, "limit": 1},
            )
        assert response.status_code == 200
        assert response.json()["offset"] == 1
        assert response.json()["total"] == 2
        assert response.json()["next_offset"] is None
        assert response.json()["entries"][0]["track"]["title"] == "Second title"

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
        assert source_count == 2
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_catalog_maintenance_repairs_metadata_and_refreshes_recent_stale_searches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = _database()
    music = Provider()
    youtube = YouTubeProvider()
    youtube.detail = ProviderTrack(
        ProviderName.YOUTUBE,
        TRACK.external_id,
        TRACK.source_url,
        TRACK.title,
        artist_text="Known artist",
        artists=(
            ProviderArtist(
                ProviderName.YOUTUBE_MUSIC,
                "UCknown",
                "Known artist",
            ),
        ),
        duration_seconds=181,
        artwork_url="https://img.example.test/cover.jpg",
        album_title="Known album",
        quality=ObservationQuality.DETAIL,
    )
    now = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(
        units,
        (music, youtube),
        clock=lambda: now[0],
        maintenance_batch=2,
        maintenance_delay=0,
    )

    async def scenario() -> None:
        discovered = await service.search("Zara Larsson")
        assert discovered.snapshot.entries[0].track.duration_seconds is None

        now[0] += timedelta(minutes=6)
        caplog.set_level("INFO", logger="nahoermaar.catalog.service")
        repaired, refreshed = await service.maintain()

        assert (repaired, refreshed) == (1, 1)
        assert youtube.track_calls == 1
        assert music.search_calls == 2
        stored = await service.track_for_source(
            discovered.snapshot.entries[0].source.id
        )
        assert stored is not None
        assert stored.duration_seconds == 181
        assert stored.album_title == "Known album"
        assert [credit.artist.name for credit in stored.artists] == ["Known artist"]
        assert any(
            "catalog.maintenance_metadata_repaired" in message
            and "title='First title'" in message
            and "added_fields=artist,duration,artwork,album" in message
            for message in caplog.messages
        )
        assert any(
            "catalog.maintenance_discovery_refreshed" in message
            and "locator='zara larsson'" in message
            for message in caplog.messages
        )
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_catalog_maintenance_does_not_count_still_incomplete_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = _database()
    music = Provider()
    youtube = YouTubeProvider()
    youtube.detail = ProviderTrack(
        ProviderName.YOUTUBE,
        TRACK.external_id,
        TRACK.source_url,
        TRACK.title,
        quality=ObservationQuality.DETAIL,
    )
    now = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(
        units,
        (music, youtube),
        clock=lambda: now[0],
        maintenance_delay=0,
    )

    async def scenario() -> None:
        discovered = await service.search("Zara Larsson")
        now[0] += timedelta(minutes=6)
        caplog.set_level("INFO", logger="nahoermaar.catalog.service")

        repaired, refreshed = await service.maintain(batch_size=1)

        assert (repaired, refreshed) == (0, 0)
        assert youtube.track_calls == 1
        assert service.maintenance_status().last_metadata_repaired == 0
        assert any(
            "catalog.maintenance_metadata_incomplete" in message
            and f"source_id={discovered.snapshot.entries[0].source.id}" in message
            and "remaining_fields=artist" in message
            for message in caplog.messages
        )
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_manual_catalog_maintenance_reports_status_and_rejects_overlap() -> None:
    database = _database()
    music = Provider()
    youtube = YouTubeProvider()
    youtube.detail = ProviderTrack(
        ProviderName.YOUTUBE,
        TRACK.external_id,
        TRACK.source_url,
        TRACK.title,
        artist_text="Known artist",
        duration_seconds=181,
        quality=ObservationQuality.DETAIL,
    )
    now = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(
        units,
        (music, youtube),
        clock=lambda: now[0],
        maintenance_delay=0,
    )

    async def scenario() -> None:
        initial = service.maintenance_status()
        assert not initial.running
        assert initial.interval_seconds == 300
        assert initial.default_batch_size == 10
        assert initial.parallel_requests == 4

        await service.search("Zara Larsson")
        now[0] += timedelta(minutes=6)
        youtube.track_started = asyncio.Event()
        youtube.track_release = asyncio.Event()

        requested = service.trigger_maintenance(batch_size=7)
        assert requested.running
        assert requested.active_batch_size == 7
        assert requested.active_trigger == "manual"
        await youtube.track_started.wait()
        while service.maintenance_status().active_processed < 1:
            await asyncio.sleep(0)
        active = service.maintenance_status()
        assert active.active_candidates == 2
        assert active.active_processed == 1

        with pytest.raises(CatalogError) as failure:
            service.trigger_maintenance(batch_size=1)
        assert failure.value.code is CatalogErrorCode.MAINTENANCE_BUSY
        assert failure.value.status == 409

        youtube.track_release.set()
        while service.maintenance_status().running:
            await asyncio.sleep(0)
        completed = service.maintenance_status()
        assert completed.last_trigger == "manual"
        assert completed.last_metadata_candidates == 1
        assert completed.last_metadata_repaired == 1
        assert completed.active_candidates == 0
        assert completed.active_processed == 0
        assert completed.last_error is None
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_catalog_maintenance_processes_a_bounded_parallel_batch() -> None:
    database = _database()
    music = Provider()
    music.extra_tracks = tuple(
        ProviderTrack(
            ProviderName.YOUTUBE,
            f"parallel{index:03d}",
            f"https://www.youtube.com/watch?v=parallel{index:03d}",
            f"Parallel {index}",
        )
        for index in range(3)
    )
    youtube = ParallelYouTubeProvider(expected_parallel=3)
    now = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(
        units,
        (music, youtube),
        clock=lambda: now[0],
        maintenance_delay=0,
        maintenance_parallel_requests=3,
    )

    async def scenario() -> None:
        await service.search("Zara Larsson")
        now[0] += timedelta(minutes=6)
        service.trigger_maintenance(batch_size=4)
        await youtube.parallel_started.wait()

        running = service.maintenance_status()
        assert running.active_candidates == 4
        assert running.active_processed == 0
        assert running.parallel_requests == 3

        youtube.parallel_release.set()
        while service.maintenance_status().running:
            await asyncio.sleep(0)

        completed = service.maintenance_status()
        assert youtube.max_active_tracks == 3
        assert completed.last_metadata_candidates == 4
        assert completed.last_metadata_repaired == 4
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_search_endpoint_pages_a_snapshot_larger_than_the_first_view() -> None:
    database = _database()
    provider = Provider()
    provider.extra_tracks = tuple(
        ProviderTrack(
            ProviderName.YOUTUBE,
            f"result{index:05d}",
            f"https://www.youtube.com/watch?v=result{index:05d}",
            f"Result {index}",
        )
        for index in range(24)
    )

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: NOW)

    async def scenario() -> None:
        api = FastAPI()
        api.include_router(catalog_router(service))
        transport = httpx.ASGITransport(app=api)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:3000",
        ) as client:
            first = await client.get("/api/catalog/search", params={"q": "Paging"})
            assert first.status_code == 200
            body = first.json()
            assert len(body["entries"]) == 20
            assert body["total"] == 25
            assert body["next_offset"] == 20

            second = await client.get(
                f"/api/catalog/search/{body['version']}",
                params={"offset": body["next_offset"], "limit": 20},
            )
            assert second.status_code == 200
            assert len(second.json()["entries"]) == 5
            assert second.json()["next_offset"] is None
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


@pytest.mark.parametrize("kind", [DiscoveryKind.SEARCH, DiscoveryKind.PLAYLIST])
def test_provider_continuation_extends_snapshot_through_api(
    kind: DiscoveryKind,
) -> None:
    database = _database()
    provider = Provider()
    next_track = ProviderTrack(
        ProviderName.YOUTUBE,
        "nexttrack01",
        "https://www.youtube.com/watch?v=nexttrack01",
        "Next title",
    )
    provider.continued_tracks = (next_track,)
    provider.search_continuation = "search-page-2"
    provider.playlist_continuation = "playlist-page-2"

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: NOW)

    async def scenario() -> None:
        first = (
            await service.search("Paging")
            if kind is DiscoveryKind.SEARCH
            else await service.playlist(PLAYLIST.source_url)
        )
        assert first.snapshot.source_has_more
        api = FastAPI()
        api.include_router(catalog_router(service))
        transport = httpx.ASGITransport(app=api)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:3000",
        ) as client:
            response = await client.post(
                f"/api/catalog/{kind.value}/{first.snapshot.id}/continue",
                params={"offset": 1, "limit": first.snapshot.limit},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["version"] != str(first.snapshot.id)
        assert body["offset"] == 1
        assert body["total"] == 2
        assert body["source_has_more"] is False
        assert [entry["track"]["title"] for entry in body["entries"]] == ["Next title"]
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


def test_audio_resolution_enriches_incomplete_discovery_metadata() -> None:
    database = _database()
    provider = Provider()
    provider.detail = ProviderTrack(
        ProviderName.YOUTUBE,
        TRACK.external_id,
        TRACK.source_url,
        TRACK.title,
        "Detail artist",
        (
            ProviderArtist(
                ProviderName.YOUTUBE,
                "UCdetail",
                "Detail artist",
            ),
        ),
        duration_seconds=181.0,
        quality=ObservationQuality.DETAIL,
    )

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: NOW)

    async def scenario() -> None:
        playlist = await service.playlist(PLAYLIST.source_url)
        discovered = playlist.snapshot.entries[0].track
        assert discovered.artists == ()

        resolved = await service.resolve_audio(discovered.id)

        assert provider.track_calls == 1
        assert resolved.track.artists[0].artist.name == "Detail artist"
        assert resolved.track.duration_seconds == 181.0
        assert resolved.source.observed_artist == "Detail artist"
        stored = (await service.tracks({discovered.id}))[discovered.id]
        assert stored.artists == resolved.track.artists
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_audio_resolution_repairs_detail_metadata_without_an_artist() -> None:
    database = _database()
    provider = Provider()
    provider.detail = ProviderTrack(
        ProviderName.YOUTUBE,
        TRACK.external_id,
        TRACK.source_url,
        "For You Pt. 1 & 2",
        uploader_name="skeler.",
        uploader_url="https://www.youtube.com/channel/UCpoKdKVhH-jcr3Pu0auKjmw",
        duration_seconds=435.0,
        quality=ObservationQuality.DETAIL,
    )

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: NOW)

    async def scenario() -> None:
        incomplete = await service.track(TRACK.source_url)
        assert incomplete.artists == ()
        provider.detail = ProviderTrack(
            ProviderName.YOUTUBE,
            TRACK.external_id,
            TRACK.source_url,
            "For You Pt. 1 & 2",
            "skeler.",
            (
                ProviderArtist(
                    ProviderName.YOUTUBE,
                    "UCpoKdKVhH-jcr3Pu0auKjmw",
                    "skeler.",
                ),
            ),
            duration_seconds=435.0,
            quality=ObservationQuality.DETAIL,
        )

        resolved = await service.resolve_audio(incomplete.id, incomplete.sources[0].id)

        assert provider.track_calls == 2
        assert [credit.artist.name for credit in resolved.track.artists] == ["skeler."]
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_radio_does_not_block_on_optional_album_or_artwork_metadata() -> None:
    database = _database()
    provider = Provider()
    provider.radio_entries = (
        ProviderTrack(
            ProviderName.YOUTUBE,
            TRACK.external_id,
            TRACK.source_url,
            "Radio result",
            "Known artist",
            (
                ProviderArtist(
                    ProviderName.YOUTUBE_MUSIC,
                    "UCknown",
                    "Known artist",
                ),
            ),
            duration_seconds=180.0,
        ),
    )

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: NOW)

    async def scenario() -> None:
        seed = await service.track(TRACK.source_url)
        provider.track_calls = 0

        page = await service.radio(source_id=seed.sources[0].id)

        assert len(page.entries) == 1
        assert provider.track_calls == 0
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_known_artist_search_returns_local_catalog_before_provider_refresh() -> None:
    database = _database()
    provider = Provider()
    provider.detail = ProviderTrack(
        ProviderName.YOUTUBE,
        TRACK.external_id,
        TRACK.source_url,
        "Hazy Mercer",
        "5l33p",
        (ProviderArtist(ProviderName.YOUTUBE_MUSIC, "UC5l33p", "5l33p"),),
        duration_seconds=180.0,
        quality=ObservationQuality.DETAIL,
    )

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(units, (provider,), clock=lambda: NOW)

    async def scenario() -> None:
        stored = await service.track(TRACK.source_url)
        assert stored.title == "Hazy Mercer"

        provider.started = asyncio.Event()
        provider.release = asyncio.Event()
        result = await service.search("5l33p")

        assert result.refreshing
        assert result.snapshot.entries[0].track.id == stored.id
        assert result.snapshot.entries[0].track.artists[0].artist.name == "5l33p"
        await provider.started.wait()
        provider.release.set()
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())
