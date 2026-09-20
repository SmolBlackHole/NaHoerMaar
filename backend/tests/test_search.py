# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio

import pytest

from nahormaar_backend import search as module
from nahormaar_backend import discovery_cache as cache_module
from nahormaar_backend.audio import TrackError
from nahormaar_backend.search import (
    CatalogBusy,
    CatalogTrack,
    SearchCatalog,
    SearchSource,
    music_track,
)


class Provider:
    def __init__(self, title: str = "Song") -> None:
        self.title = title
        self.calls: list[tuple[str, int]] = []
        self.release: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.fail = False
        self.cancelled = False

    async def search(self, query: str, limit: int) -> tuple[CatalogTrack, ...]:
        self.calls.append((query, limit))
        self.started.set()
        try:
            if self.release is not None:
                await self.release.wait()
            if self.fail:
                raise TrackError("Unavailable")
            return tuple(CatalogTrack(index=i + 1, title=self.title) for i in range(23))
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_default_music_separate_sources_and_stable_cached_pages() -> None:
    async def scenario() -> None:
        music, videos = Provider("Song"), Provider("Video")
        catalog = SearchCatalog(
            {SearchSource.MUSIC: music, SearchSource.VIDEOS: videos}
        )
        try:
            first = await catalog.search("  A   song  ")
            music.title = "Changed upstream"
            second = await catalog.search("a SONG", 10)
            last = await catalog.search("A song", 20)
            assert first.entries[0].title == second.entries[0].title == "Song"
            assert [entry.index for entry in second.entries] == list(range(11, 21))
            assert len(last.entries) == 3 and last.next_offset is None
            assert music.calls == [("A song", 100)]
            video = await catalog.search("A song", source=SearchSource.VIDEOS)
            assert video.entries[0].title == "Video"
            assert videos.calls == [("A song", 100)]
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_cache_expires_and_evicts_least_recent_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        clock = [0.0]
        monkeypatch.setattr(cache_module, "monotonic", lambda: clock[0])
        monkeypatch.setattr(module, "SEARCH_CACHE_LIMIT", 2)
        provider = Provider()
        catalog = SearchCatalog({SearchSource.MUSIC: provider})
        try:
            for query in ("a", "b", "a", "c", "a", "b"):
                await catalog.search(query)
            assert [call[0] for call in provider.calls] == ["a", "b", "c", "b"]
            clock[0] = module.SEARCH_CACHE_TTL + 1
            await catalog.search("b")
            await asyncio.sleep(0)
            assert len(provider.calls) == 5
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_identical_requests_share_work_and_one_cancel_does_not_cancel_other() -> None:
    async def scenario() -> None:
        provider = Provider()
        provider.release = asyncio.Event()
        catalog = SearchCatalog({SearchSource.MUSIC: provider})
        first = asyncio.create_task(catalog.search("song"))
        await provider.started.wait()
        second = asyncio.create_task(catalog.search("SONG"))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not provider.cancelled
        provider.release.set()
        assert len((await second).entries) == 10
        assert len(provider.calls) == 1
        await catalog.close()

    asyncio.run(scenario())


def test_failure_is_retryable_and_shutdown_cancels_worker() -> None:
    async def scenario() -> None:
        provider = Provider()
        catalog = SearchCatalog({SearchSource.MUSIC: provider})
        provider.fail = True
        with pytest.raises(TrackError):
            await catalog.search("song")
        provider.fail = False
        assert (await catalog.search("song")).entries
        assert len(provider.calls) == 2
        provider.release = asyncio.Event()
        provider.started.clear()
        pending = asyncio.create_task(catalog.search("other"))
        await provider.started.wait()
        await catalog.close()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert provider.cancelled
        with pytest.raises(CatalogBusy):
            await catalog.search("song")

    asyncio.run(scenario())


def test_music_metadata_preserves_unicode_artists_and_music_links() -> None:
    track = music_track(
        {
            "videoId": "Pqp9fDRp1lw",
            "title": "Амура",
            "artists": [{"name": "Амура", "id": "UCexample"}, {"name": "MRJay"}],
            "duration_seconds": 180,
            "thumbnails": [{"url": "https://example.org/cover.jpg"}],
        },
        1,
    )
    assert track.title == "Амура" and track.artist == "Амура, MRJay"
    assert track.source_url == "https://music.youtube.com/watch?v=Pqp9fDRp1lw"
    assert track.uploader_url == "https://music.youtube.com/channel/UCexample"
    assert track.duration_seconds == 180
    assert track.thumbnail_url == "https://example.org/cover.jpg"
    assert music_track({"videoId": "invalid"}, 1).unavailable
    assert music_track({"videoId": "Pqp9fDRp1lw", "isAvailable": False}, 1).unavailable
