# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
from pathlib import Path

import pytest

from nahoermaar.catalog.domain import MediaKind, ObservationQuality, ProviderName
from nahoermaar.catalog.providers import ProviderError
from nahoermaar.integrations.processes import ProcessResult
from nahoermaar.integrations.youtube import YouTubeMusicProvider, YouTubeProvider


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, args: tuple[str, ...], *, timeout: float) -> ProcessResult:
        assert timeout == 5
        self.calls.append(args)
        payload: dict[str, object]
        if any(argument.startswith("ytsearch") for argument in args):
            payload = {
                "entries": [
                    {
                        "id": "abcdefghijk",
                        "title": "Video search title",
                        "duration": 180,
                    }
                ]
            }
        elif "radio" in args:
            payload = {
                "entries": [
                    {
                        "videoId": "radiotrack1",
                        "title": "Radio title",
                        "artists": [{"id": "UCradio", "name": "Radio artist"}],
                    }
                ]
            }
        elif "search" in args:
            payload = {
                "entries": [
                    {
                        "videoId": "abcdefghijk",
                        "title": "Search title",
                        "artists": [{"id": "UCartist", "name": "Artist"}],
                        "duration": "3:01",
                        "thumbnails": [
                            {"url": "https://img/small"},
                            {"url": "https://img/large"},
                        ],
                        "album": {"name": "Album"},
                    }
                ]
            }
        elif "--yes-playlist" in args:
            payload = {
                "id": "PLabcdefghijk",
                "title": "Playlist",
                "entries": [{"id": "abcdefghijk", "title": "Playlist title"}],
            }
        elif any("skeler00001" in argument for argument in args):
            payload = {
                "id": "skeler00001",
                "title": "For You Pt. 1 & 2",
                "duration": 435,
                "thumbnail": "https://img/skeler",
                "uploader": "skeler.",
                "channel_id": "UCpoKdKVhH-jcr3Pu0auKjmw",
                "channel_url": "https://www.youtube.com/channel/UCpoKdKVhH-jcr3Pu0auKjmw",
            }
        else:
            payload = {
                "id": "abcdefghijk",
                "title": "Detail title",
                "duration": 182,
                "thumbnail": "https://img/detail",
                "artist": "Detail artist",
                "channel_id": "UCdetail",
                "uploader": "Uploader",
                "upload_date": "20260924",
            }
        return ProcessResult(0, json.dumps(payload).encode(), b"")


class StuckRunner:
    def __init__(self) -> None:
        self.cancelled = False

    async def __call__(self, args: tuple[str, ...], *, timeout: float) -> ProcessResult:
        del args, timeout
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("The stuck runner unexpectedly resumed.")


def test_youtube_provider_translates_search_playlist_and_details() -> None:
    runner = Runner()
    provider = YouTubeMusicProvider(Path("node"), timeout=5, runner=runner)
    video_provider = YouTubeProvider(Path("node"), timeout=5, runner=runner)
    track_url = "https://music.youtube.com/watch?v=abcdefghijk&list=PLabcdefghijk"
    playlist_url = "https://youtube.com/playlist?list=PLabcdefghijk"

    async def scenario() -> None:
        track_reference = provider.identify(track_url, kind=MediaKind.TRACK)
        playlist_reference = provider.identify(playlist_url, kind=MediaKind.PLAYLIST)
        assert track_reference is not None
        assert playlist_reference is not None
        assert track_reference.provider is ProviderName.YOUTUBE
        assert playlist_reference.external_id == "PLabcdefghijk"

        search = await provider.search("query", limit=10)
        assert search.entries[0].title == "Search title"
        assert search.entries[0].artists[0].external_id == "UCartist"
        assert search.entries[0].duration_seconds == 181
        assert search.entries[0].artwork_url == "https://img/large"

        video_search = await video_provider.search("query", limit=10)
        assert video_search.entries[0].title == "Video search title"
        assert provider.key == "youtube_music"
        assert video_provider.key == "youtube"

        playlist = await provider.playlist(playlist_reference, limit=10)
        assert playlist.title == "Playlist"
        assert playlist.page.entries[0].title == "Playlist title"

        radio = await provider.radio(track_reference, limit=10)
        assert radio.entries[0].title == "Radio title"

        detail = await provider.track(track_reference)
        assert detail.title == "Detail title"
        assert detail.artist_text == "Detail artist"
        assert detail.artists[0].external_id == "UCdetail"
        assert detail.artists[0].name == "Detail artist"
        assert detail.quality is ObservationQuality.DETAIL
        assert detail.release_date is not None

        uploader_reference = provider.identify(
            "https://www.youtube.com/watch?v=skeler00001",
            kind=MediaKind.TRACK,
        )
        assert uploader_reference is not None
        uploader_detail = await provider.track(uploader_reference)
        assert uploader_detail.artist_text == "skeler."
        assert uploader_detail.artists[0].name == "skeler."
        assert uploader_detail.artists[0].external_id == "UCpoKdKVhH-jcr3Pu0auKjmw"
        await provider.close()
        await video_provider.close()

    asyncio.run(scenario())


def test_youtube_provider_watchdog_cancels_a_stuck_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nahoermaar.integrations.youtube as youtube_module

    monkeypatch.setattr(youtube_module, "_PROCESS_TIMEOUT_GRACE_SECONDS", 0.01)
    runner = StuckRunner()
    provider = YouTubeProvider(Path("node"), timeout=0.01, runner=runner)
    reference = provider.identify(
        "https://www.youtube.com/watch?v=abcdefghijk",
        kind=MediaKind.TRACK,
    )
    assert reference is not None

    async def scenario() -> None:
        with pytest.raises(ProviderError, match="too long") as caught:
            await provider.track(reference)
        assert caught.value.retryable is True
        assert runner.cancelled is True
        await provider.close()

    asyncio.run(scenario())
