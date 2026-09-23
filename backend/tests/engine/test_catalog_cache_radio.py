# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from nahormaar_backend.engine.catalog import Catalog
from nahormaar_backend.engine.domain.catalog import (
    MediaKind,
    MediaReference,
    TrackFinding,
    UnavailableFinding,
)
from nahormaar_backend.engine.domain.tracks import MediaIdentity
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.persistence import TrackRepository
from nahormaar_backend.engine.providers import (
    RadioProvider,
    ProviderError,
    UnsupportedCapability,
)
from nahormaar_backend.engine.youtube import YouTubeMusicProvider, YouTubeProvider

from .database import isolated_database
from .test_catalog import TIME, REFERENCE
from .test_discovery import OTHER, music_song, response, song
from .test_youtube_provider import IDENTITY, PLAYLIST_ID, URL, FixtureRunner, details


@pytest.mark.parametrize("playlist", [False, True])
def test_radio_uses_native_seed_and_translates_candidates_without_queueing(
    tmp_path: Path, playlist: bool
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio.db") as sessions:
            candidate = music_song()
            candidate.pop("duration_seconds", None)
            candidate.pop("duration", None)
            candidate["length"] = "3:24"
            candidate["thumbnail"] = [{"url": "https://image.example/cover"}]
            candidate.pop("thumbnails", None)
            runner = FixtureRunner(
                response(
                    [], tracks=[candidate, None, candidate, music_song(videoId=OTHER)]
                )
            )
            provider = YouTubeMusicProvider(Path("node"), runner=runner)
            assert isinstance(provider, RadioProvider)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            seed = (
                MediaReference(
                    MediaIdentity("youtube", PLAYLIST_ID),
                    MediaKind.PLAYLIST,
                    "https://music.youtube.com/playlist?list=" + PLAYLIST_ID,
                )
                if playlist
                else REFERENCE
            )
            page = await provider.radio_next(seed, limit=3)
            first = page.entries[0]
            assert isinstance(first, TrackFinding)
            assert first.metadata.duration_seconds == 204
            assert first.metadata.thumbnail_url == "https://image.example/cover"
            assert page.entries[0] == page.entries[2]
            assert isinstance(page.entries[1], UnavailableFinding)
            assert page.continuation is None
            recommendations = await catalog.radio_next(seed, limit=3)
            assert len(recommendations.tracks) == 2
            assert recommendations.tracks[0].id == recommendations.tracks[1].id
            args = runner.calls[0][0]
            assert args[-3:] == (seed.kind.value, seed.identity.external_id, "3")
            async with sessions.begin() as session:
                stored = await TrackRepository(session).find(IDENTITY)
                assert stored is not None and stored.checked_at is None
                assert (
                    await TrackRepository(session).find(MediaIdentity("youtube", OTHER))
                    is None
                )
            with pytest.raises(UnsupportedCapability, match="native pages"):
                await catalog.radio_next(seed, continuation="invented")
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_radio_rejects_invalid_seed_capability_and_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio.db") as sessions:
            runner = FixtureRunner(response([]))
            provider = YouTubeMusicProvider(Path("node"), runner=runner)
            catalog = Catalog(
                (provider, YouTubeProvider(Path("node"), runner=runner)),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            with pytest.raises(UnsupportedCapability, match="recommendations"):
                await catalog.radio_next(REFERENCE, provider_key="youtube")
            with pytest.raises(ValueError, match="identity"):
                await catalog.radio_next(
                    MediaReference(
                        MediaIdentity("youtube", OTHER), MediaKind.TRACK, URL
                    )
                )
            assert runner.calls == []
            with pytest.raises(ProviderError, match="invalid radio"):
                await catalog.radio_next(REFERENCE)
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_catalog_cache_keys_use_canonical_links_and_keep_newer_persistent_metadata(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = TIME
        async with isolated_database(tmp_path / "cache.db") as sessions:
            runner = FixtureRunner(details(title="Original"))
            provider = YouTubeProvider(Path("node"), runner=runner)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: now),
                clock=lambda: now,
            )
            first = await catalog.track(URL)
            assert (
                await catalog.track("https://youtu.be/" + IDENTITY.external_id)
            ) == first
            assert len(runner.calls) == 1
            now += timedelta(seconds=1)
            runner.result = details(title="Enriched during playback")
            await catalog.resolve_audio(URL)
            current = await catalog.track(URL)
            assert (
                current.id == first.id
                and current.metadata.title == "Enriched during playback"
            )
            assert current.checked_at == now
            assert len(runner.calls) == 2
            await catalog.close()
            # Closing/evicting observations never deletes durable tracks.
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(first.id) == current
            await provider.close()

    asyncio.run(scenario())


def test_search_and_playlist_snapshots_refresh_without_replacing_visible_occurrences(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = 0.0
        async with isolated_database(tmp_path / "cache.db") as sessions:
            runner = FixtureRunner(response([music_song()]))
            provider = YouTubeMusicProvider(Path("node"), runner=runner)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
                timer=lambda: now,
            )
            search = await catalog.search("  some   music ")
            assert (await catalog.search("some music")).version == search.version
            assert len(runner.calls) == 1
            runner.result = response(
                [song(), song()], _type="playlist", id=PLAYLIST_ID, title="List"
            )
            url = "https://music.youtube.com/playlist?list=" + PLAYLIST_ID
            first = await catalog.playlist(url)
            assert (
                await catalog.playlist(
                    "https://youtube.com/playlist?list=" + PLAYLIST_ID
                )
            ).version == first.version
            runner.result = response(
                [song(OTHER), song()], _type="playlist", id=PLAYLIST_ID, title="Changed"
            )
            now = 61
            assert (await catalog.playlist(url)).version == first.version
            # Yield until the controlled fixture's short metadata transaction completes.
            async with asyncio.timeout(3):
                while catalog.refresh_status(first.version, playlist=True).refreshing:
                    await asyncio.sleep(0.001)
            latest = catalog.refresh_status(first.version, playlist=True).latest_version
            assert latest != first.version
            assert (
                catalog.playlist_snapshot(first.version).value.page.entries[0]
                == catalog.playlist_snapshot(first.version).value.page.entries[1]
            )
            assert catalog.playlist_snapshot(latest).value.title == "Changed"
            assert catalog.search_snapshot(search.version) == search
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())
