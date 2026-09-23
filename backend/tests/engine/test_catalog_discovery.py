# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, SessionTransaction

from nahormaar_backend.engine.catalog import Catalog
from nahormaar_backend.engine.domain.catalog import (
    MediaKind,
    MediaReference,
    PlaylistPage,
    TrackFinding,
    TrackPage,
    UnavailableFinding,
)
from nahormaar_backend.engine.domain.metadata import (
    MetadataKind,
    MetadataSource,
    TrackMetadata,
)
from nahormaar_backend.engine.domain.tracks import MediaIdentity
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.persistence import ArtistRepository, Base, TrackRepository
from nahormaar_backend.engine.providers import ProviderError, UnsupportedCapability
from nahormaar_backend.engine.youtube import YouTubeMusicProvider, YouTubeProvider
from nahormaar_backend.integrations.processes import ProcessResult

from .database import isolated_database
from .test_catalog import TIME, REFERENCE, DetailsOnly
from .test_discovery import OTHER, music_song, response, song
from .test_youtube_provider import IDENTITY, PLAYLIST_ID, URL, FixtureRunner


def test_search_defaults_to_music_and_ingests_discovery_without_losing_details(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = TIME
        async with isolated_database(tmp_path / "catalog.db") as sessions:
            metadata = MetadataStore(sessions, clock=lambda: now)
            (known,) = await metadata.remember(
                (
                    TrackFinding(
                        REFERENCE,
                        TrackMetadata(title="Detailed title", duration_seconds=200),
                    ),
                ),
                source=MetadataSource("youtube", MetadataKind.DETAIL, now),
            )
            music_runner = FixtureRunner(
                response([music_song(), music_song(videoId=OTHER), None, music_song()])
            )
            video_runner = FixtureRunner(
                response([song(title="Worse flat title", uploader="Uploader")])
            )
            music = YouTubeMusicProvider(Path("node"), runner=music_runner)
            video = YouTubeProvider(Path("node"), runner=video_runner)
            # Defaults do not accidentally depend on registration order.
            catalog = Catalog((video, music), metadata, clock=lambda: now)
            now += timedelta(seconds=10)
            page = (await catalog.search(" song ", limit=4)).value
            assert len(page.entries) == 4 and page.entries[0] == page.entries[3]
            assert isinstance(page.entries[2], UnavailableFinding)
            assert len(music_runner.calls) == 1 and video_runner.calls == []
            async with sessions.begin() as session:
                stored = await TrackRepository(session).get(known.id)
                discovered = await TrackRepository(session).find(
                    MediaIdentity("youtube", OTHER)
                )
                assert stored is not None and discovered is not None
                assert (
                    stored.metadata.title == "Detailed title"
                    and stored.metadata.duration_seconds == 200
                )
                assert stored.checked_at == TIME
                assert discovered.checked_at is None
                assert discovered.provenance.title == MetadataSource(
                    "youtube_music", MetadataKind.DISCOVERY, now
                )
                assert len(discovered.artist_ids) == 2
                for artist_id in discovered.artist_ids:
                    artist = await ArtistRepository(session).get(artist_id)
                    assert artist is not None and artist.name == "Same name"
            await catalog.search("song", provider_key="youtube")
            assert len(video_runner.calls) == 1
            now += timedelta(seconds=10)
            music_runner.result = response(
                [
                    music_song(
                        videoId=OTHER,
                        artists=[
                            {"id": "UCFirst", "name": "Renamed"},
                            {"name": "Unidentified"},
                        ],
                    )
                ]
            )
            await catalog.search("song", limit=1)
            async with sessions.begin() as session:
                refreshed = await TrackRepository(session).find(
                    MediaIdentity("youtube", OTHER)
                )
                assert (
                    refreshed is not None
                    and refreshed.artist_ids == discovered.artist_ids
                )
                assert refreshed.metadata.artist == "Renamed, Unidentified"
                assert refreshed.provenance.artists == discovered.provenance.artists
                enriched = await TrackRepository(session).get(known.id)
                assert (
                    enriched is not None and enriched.metadata.title == "Detailed title"
                )
                assert enriched.metadata.uploader == "Uploader"
                for table_name, expected in {
                    "tracks": 2,
                    "artists": 2,
                    "queue_entries": 0,
                    "playback_records": 0,
                }.items():
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(
                                Base.metadata.tables[table_name]
                            )
                        )
                        == expected
                    )
            await asyncio.gather(video.close(), music.close())

    asyncio.run(scenario())


def test_playlist_routing_preserves_duplicate_slots_and_does_not_ingest_lookahead(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.db") as sessions:
            runner = FixtureRunner(
                response(
                    [song(), None, song(), song(OTHER)],
                    _type="playlist",
                    id=PLAYLIST_ID,
                    title="List",
                )
            )
            provider = YouTubeMusicProvider(Path("node"), runner=runner)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            mixed = URL + "&list=" + PLAYLIST_ID
            first = (await catalog.playlist(mixed, limit=3)).value
            assert first.page.entries[0] == first.page.entries[2]
            assert isinstance(first.page.entries[1], UnavailableFinding)
            assert first.page.continuation is not None
            async with sessions.begin() as session:
                stored = await TrackRepository(session).find(IDENTITY)
                assert stored is not None and stored.checked_at is None
                assert stored.provenance.title == MetadataSource(
                    "youtube_music", MetadataKind.DISCOVERY, TIME
                )
                assert (
                    await TrackRepository(session).find(MediaIdentity("youtube", OTHER))
                    is None
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(Base.metadata.tables["tracks"])
                    )
                    == 1
                )
            runner.result = response(
                [song(OTHER)],
                code=1,
                _type="playlist",
                id=PLAYLIST_ID,
                requested_entries=[1],
            )
            partial = (await catalog.playlist(mixed, limit=1)).value
            assert partial.page.error and len(partial.page.entries) == 1
            async with sessions.begin() as session:
                assert (
                    await TrackRepository(session).find(MediaIdentity("youtube", OTHER))
                    is not None
                )
                for table_name in ("queue_entries", "playback_records"):
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(
                                Base.metadata.tables[table_name]
                            )
                        )
                        == 0
                    )
            await provider.close()

    asyncio.run(scenario())


def test_discovery_rejects_missing_capabilities_and_wrong_playlist_identity(
    tmp_path: Path,
) -> None:
    class WrongPlaylist(YouTubeProvider):
        async def playlist(
            self,
            identity: MediaIdentity,
            *,
            limit: int,
            continuation: str | None = None,
        ) -> PlaylistPage:
            reference = MediaReference(
                MediaIdentity("youtube", "PLdifferent123"),
                MediaKind.PLAYLIST,
                "https://youtube.com/playlist?list=PLdifferent123",
            )
            return PlaylistPage(
                reference, "Wrong", TrackPage((TrackFinding(REFERENCE),))
            )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.db") as sessions:
            metadata = MetadataStore(sessions, clock=lambda: TIME)
            unsupported = Catalog(
                (DetailsOnly("youtube_music", TrackFinding(REFERENCE)),),
                metadata,
                clock=lambda: TIME,
            )
            with pytest.raises(UnsupportedCapability, match="cannot search"):
                await unsupported.search("song")
            with pytest.raises(UnsupportedCapability, match="Unknown"):
                await unsupported.search("song", provider_key="spotify")
            with pytest.raises(UnsupportedCapability):
                await unsupported.playlist(URL)
            runner = FixtureRunner(response([]))
            catalog = Catalog(
                (WrongPlaylist(Path("node"), runner=runner),),
                metadata,
                clock=lambda: TIME,
            )
            with pytest.raises(ProviderError, match="different playlist identity"):
                await catalog.playlist(
                    "https://youtube.com/playlist?list=" + PLAYLIST_ID
                )
            with pytest.raises(UnsupportedCapability, match="Unknown"):
                await catalog.search("song")  # Never silently fall back to Videos.
            with pytest.raises(UnsupportedCapability, match="not supported"):
                await catalog.playlist(URL)
            assert runner.calls == []
            async with sessions.begin() as session:
                assert await TrackRepository(session).find(IDENTITY) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("playlist", [False, True])
def test_discovery_io_stays_outside_metadata_transactions(
    tmp_path: Path, playlist: bool
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.db") as sessions:
            started = asyncio.Event()
            transactions: list[SessionTransaction] = []

            def transaction_created(
                session: Session, transaction: SessionTransaction
            ) -> None:
                transactions.append(transaction)

            async def pending(
                args: tuple[str, ...], *, timeout: float
            ) -> ProcessResult:
                started.set()
                await asyncio.Event().wait()
                return response([])

            provider = YouTubeMusicProvider(Path("node"), runner=pending)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            event.listen(Session, "after_transaction_create", transaction_created)
            try:
                task = asyncio.create_task(
                    catalog.playlist("https://youtube.com/playlist?list=" + PLAYLIST_ID)
                    if playlist
                    else catalog.search("song")
                )
                await started.wait()
                assert not transactions
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await catalog.close()
                await provider.close()
                assert not transactions

                async def successful(
                    args: tuple[str, ...], *, timeout: float
                ) -> ProcessResult:
                    assert not transactions
                    return (
                        response([song()], _type="playlist", id=PLAYLIST_ID)
                        if playlist
                        else response([music_song()])
                    )

                ready = YouTubeMusicProvider(Path("node"), runner=successful)
                ready_catalog = Catalog(
                    (ready,),
                    MetadataStore(sessions, clock=lambda: TIME),
                    clock=lambda: TIME,
                )
                if playlist:
                    await ready_catalog.playlist(
                        "https://youtube.com/playlist?list=" + PLAYLIST_ID
                    )
                else:
                    await ready_catalog.search("song")
                assert transactions
                await ready.close()
            finally:
                event.remove(Session, "after_transaction_create", transaction_created)

    asyncio.run(scenario())
