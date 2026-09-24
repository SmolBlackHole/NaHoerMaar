# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
from pathlib import Path

from nahoermaar.catalog.domain import MediaKind, ObservationQuality, ProviderName
from nahoermaar.integrations.processes import ProcessResult
from nahoermaar.integrations.youtube import YouTubeProvider


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, args: tuple[str, ...], *, timeout: float) -> ProcessResult:
        assert timeout == 5
        self.calls.append(args)
        if "search" in args:
            payload: dict[str, object] = {
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
        else:
            payload = {
                "id": "abcdefghijk",
                "title": "Detail title",
                "duration": 182,
                "thumbnail": "https://img/detail",
                "uploader": "Uploader",
                "upload_date": "20260924",
            }
        return ProcessResult(0, json.dumps(payload).encode(), b"")


def test_youtube_provider_translates_search_playlist_and_details() -> None:
    runner = Runner()
    provider = YouTubeProvider(Path("node"), timeout=5, runner=runner)
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

        playlist = await provider.playlist(playlist_reference, limit=10)
        assert playlist.title == "Playlist"
        assert playlist.page.entries[0].title == "Playlist title"

        detail = await provider.track(track_reference)
        assert detail.title == "Detail title"
        assert detail.quality is ObservationQuality.DETAIL
        assert detail.release_date is not None
        await provider.close()

    asyncio.run(scenario())
