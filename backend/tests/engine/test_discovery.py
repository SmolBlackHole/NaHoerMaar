# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
import sys
from pathlib import Path
from typing import cast

import pytest

from nahormaar_backend.engine.domain.catalog import (
    MediaKind,
    TrackFinding,
    UnavailableFinding,
)
from nahormaar_backend.engine.domain.tracks import ArtistIdentity, MediaIdentity
from nahormaar_backend.engine.providers import ProviderError, UnsupportedCapability
from nahormaar_backend.engine.youtube import YouTubeMusicProvider, YouTubeProvider
from nahormaar_backend.integrations.processes import ProcessResult

from .test_youtube_provider import FixtureRunner, IDENTITY, PLAYLIST_ID, URL, details

PLAYLIST = MediaIdentity("youtube", PLAYLIST_ID)
OTHER = "abcdefghijk"


def response(entries: object, *, code: int = 0, **fields: object) -> ProcessResult:
    return ProcessResult(
        code, json.dumps({"entries": entries, **fields}).encode(), b"raw diagnostic"
    )


def song(
    identifier: str = IDENTITY.external_id, **changes: object
) -> dict[str, object]:
    return {"id": identifier, "title": " Song ", "duration": 180, **changes}


def music_song(**changes: object) -> dict[str, object]:
    return {
        "videoId": IDENTITY.external_id,
        "title": " Я хочу любить ",
        "artists": [
            {"id": "UCFirst", "name": " Same name "},
            {"id": "UCSecond", "name": " Same name "},
            {"id": "UCFirst", "name": "Duplicate"},
        ],
        "duration_seconds": 181,
        "thumbnails": [{"url": " small "}, {"url": " large "}],
        **changes,
    }


def test_video_search_is_bounded_and_preserves_unavailable_slots_and_duplicates() -> (
    None
):
    async def scenario() -> None:
        runner = FixtureRunner(response([song(), None, song(), song(OTHER), song()]))
        provider = YouTubeProvider(Path("node"), runner=runner)
        page = await provider.search("  Я\n хочу   любить ", limit=4)
        assert len(page.entries) == 4
        assert page.entries[0] == page.entries[2]
        assert isinstance(page.entries[1], UnavailableFinding)
        assert page.continuation is None and page.error is None
        args, _ = runner.calls[0]
        assert args[-2:] == ("--", "ytsearch4:Я хочу любить")
        assert "--flat-playlist" in args and "--dump-single-json" in args
        assert "--simulate" in args and "--ignore-config" in args
        await provider.close()

    asyncio.run(scenario())


def test_music_search_preserves_artist_identity_and_uses_isolated_worker() -> None:
    async def scenario() -> None:
        runner = FixtureRunner(response([music_song(), music_song(videoId=OTHER)]))
        provider = YouTubeMusicProvider(Path("node"), runner=runner)
        page = await provider.search("  artist  song ", limit=1)
        assert len(page.entries) == 1
        entry = page.entries[0]
        assert isinstance(entry, TrackFinding)
        assert entry.reference.identity == IDENTITY
        assert (
            entry.reference.source_url
            == "https://music.youtube.com/watch?v=" + IDENTITY.external_id
        )
        assert entry.metadata.title == "Я хочу любить"
        assert entry.metadata.artist == "Same name, Same name"
        assert entry.metadata.thumbnail_url == "large"
        assert entry.metadata.duration_seconds == 181
        assert entry.metadata.uploader is None and entry.metadata.uploader_url is None
        assert entry.artists is not None
        assert tuple(artist.identity for artist in entry.artists) == (
            ArtistIdentity("youtube", "UCFirst"),
            ArtistIdentity("youtube", "UCSecond"),
        )
        assert runner.calls[0][0] == (
            sys.executable,
            "-m",
            "nahormaar_backend.integrations.music_search",
            "artist song",
            "1",
        )
        # Music shares the same detail/audio and link identity implementation.
        runner.result = details()
        assert (
            await provider.track(IDENTITY)
        ).reference.identity == entry.reference.identity
        assert (
            await provider.resolve_audio(IDENTITY)
        ).track.reference.identity == IDENTITY
        assert provider.identify(URL) is not None
        await provider.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "artists",
    [
        None,
        "unknown",
        [None],
        [{"id": "UCFirst", "name": "Known"}, {"name": "Unidentified, artist"}],
        [{"id": "UCFirst"}],
    ],
)
def test_incomplete_music_credits_do_not_claim_a_complete_artist_list(
    artists: object,
) -> None:
    async def scenario() -> None:
        provider = YouTubeMusicProvider(
            Path("node"), runner=FixtureRunner(response([music_song(artists=artists)]))
        )
        entry = (await provider.search("song", limit=1)).entries[0]
        assert isinstance(entry, TrackFinding) and entry.artists is None
        if isinstance(artists, list) and len(cast(list[object], artists)) == 2:
            assert entry.metadata.artist == "Known, Unidentified, artist"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        ("3:02", 182),
        ("1:02:03", 3723),
        ("180", 180),
        ("not known", None),
        ("1::2", None),
        ("1:2:3:4", None),
    ],
)
def test_music_duration_fallback(duration: str, expected: int | None) -> None:
    async def scenario() -> None:
        provider = YouTubeMusicProvider(
            Path("node"),
            runner=FixtureRunner(
                response(
                    [music_song(duration_seconds=None, duration=duration, artists=[])]
                )
            ),
        )
        entry = (await provider.search("song", limit=1)).entries[0]
        assert isinstance(entry, TrackFinding)
        assert entry.metadata.duration_seconds == expected
        assert entry.artists == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_type", [YouTubeProvider, YouTubeMusicProvider])
@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_search_and_playlist_limits_fail_before_io(
    provider_type: type[YouTubeProvider], limit: int
) -> None:
    async def scenario() -> None:
        runner = FixtureRunner(response([]))
        provider = provider_type(Path("node"), runner=runner)
        with pytest.raises(ValueError, match="limit"):
            await provider.search("song", limit=limit)
        with pytest.raises(ValueError, match="limit"):
            await provider.playlist(PLAYLIST, limit=limit)
        assert not runner.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_type", [YouTubeProvider, YouTubeMusicProvider])
def test_search_rejects_invalid_queries_and_fabricated_continuations(
    provider_type: type[YouTubeProvider],
) -> None:
    async def scenario() -> None:
        runner = FixtureRunner(response([]))
        provider = provider_type(Path("node"), runner=runner)
        for query in (" ", "x" * 201):
            with pytest.raises(ValueError, match="1 to 200"):
                await provider.search(query, limit=10)
        with pytest.raises(UnsupportedCapability, match="result pool"):
            await provider.search("song", limit=10, continuation="not-a-native-cursor")
        assert runner.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_type", [YouTubeProvider, YouTubeMusicProvider])
@pytest.mark.parametrize(
    "result",
    [
        response(None),
        response({}),
        ProcessResult(0, b"garbage", b""),
        ProcessResult(0, b"\xff", b""),
        ProcessResult(0, b"[]", b""),
    ],
)
def test_search_rejects_malformed_top_level_responses(
    provider_type: type[YouTubeProvider], result: ProcessResult
) -> None:
    provider = provider_type(Path("node"), runner=FixtureRunner(result))
    with pytest.raises(ProviderError):
        asyncio.run(provider.search("song", limit=10))


def test_unavailable_music_and_video_results_keep_their_slots() -> None:
    async def scenario() -> None:
        video = YouTubeProvider(
            Path("node"),
            runner=FixtureRunner(
                response(
                    [
                        song(availability="private"),
                        song(title="[Deleted video]"),
                        song(is_live=True),
                        {"title": "No identity"},
                        "malformed",
                    ]
                )
            ),
        )
        music = YouTubeMusicProvider(
            Path("node"),
            runner=FixtureRunner(
                response(
                    [
                        music_song(isAvailable=False),
                        music_song(isLive=True),
                        music_song(videoId=None),
                        None,
                    ]
                )
            ),
        )
        for provider, count in ((video, 5), (music, 4)):
            page = await provider.search("song", limit=10)
            assert len(page.entries) == count
            assert all(isinstance(entry, UnavailableFinding) for entry in page.entries)
            assert page.entries[0].reference is not None
            assert page.entries[-1].reference is None

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_type", [YouTubeProvider, YouTubeMusicProvider])
def test_playlist_lookahead_continuation_and_exhaustion_preserve_occurrences(
    provider_type: type[YouTubeProvider],
) -> None:
    async def scenario() -> None:
        runner = FixtureRunner(
            response(
                [song(), None, song(), song(OTHER)],
                _type="playlist",
                id=PLAYLIST_ID,
                title=" My playlist ",
                requested_entries=[1, 2, 3, 4],
            )
        )
        provider = provider_type(Path("node"), runner=runner)
        page = await provider.playlist(PLAYLIST, limit=3)
        assert (
            page.reference.identity == PLAYLIST
            and page.reference.kind is MediaKind.PLAYLIST
        )
        assert page.title == "My playlist"
        assert len(page.page.entries) == 3
        assert page.page.entries[0] == page.page.entries[2]
        assert isinstance(page.page.entries[1], UnavailableFinding)
        assert page.page.continuation is not None
        first_args = runner.calls[0][0]
        assert first_args[first_args.index("--playlist-items") + 1] == "1:4"
        assert first_args[-1] == "https://www.youtube.com/playlist?list=" + PLAYLIST_ID
        runner.result = response(
            [song(OTHER), song()],
            _type="playlist",
            id=PLAYLIST_ID,
            requested_entries=[4, 5],
        )
        next_page = await provider.playlist(
            PLAYLIST, limit=3, continuation=page.page.continuation
        )
        assert len(next_page.page.entries) == 2
        assert next_page.page.entries[0].reference is not None
        assert next_page.page.entries[0].reference.identity.external_id == OTHER
        assert next_page.page.continuation is None
        next_args = runner.calls[1][0]
        assert next_args[next_args.index("--playlist-items") + 1] == "4:7"
        await provider.close()

    asyncio.run(scenario())


def test_playlist_preserves_known_source_gaps_as_unavailable_occurrences() -> None:
    async def scenario() -> None:
        runner = FixtureRunner(
            response(
                [song(), song(OTHER)],
                _type="playlist",
                id=PLAYLIST_ID,
                requested_entries=[1, 4],
            )
        )
        page = await YouTubeProvider(Path("node"), runner=runner).playlist(
            PLAYLIST, limit=3
        )
        assert len(page.page.entries) == 3
        assert all(
            isinstance(entry, UnavailableFinding) for entry in page.page.entries[1:]
        )
        assert page.page.continuation is not None

    asyncio.run(scenario())


def test_empty_playlist_keeps_its_title_and_partial_results_do_not_claim_success() -> (
    None
):
    async def scenario() -> None:
        runner = FixtureRunner(
            response([], _type="playlist", id=PLAYLIST_ID, title="Empty")
        )
        provider = YouTubeProvider(Path("node"), runner=runner)
        empty = await provider.playlist(PLAYLIST, limit=2)
        assert empty.title == "Empty" and empty.page.entries == ()
        assert empty.page.continuation is None and empty.page.error is None
        runner.result = response(
            [song(), None, song()], code=1, _type="playlist", id=PLAYLIST_ID
        )
        partial = await provider.playlist(PLAYLIST, limit=2)
        assert len(partial.page.entries) == 2
        assert partial.page.error and "raw diagnostic" not in partial.page.error
        assert partial.page.continuation is None
        runner.result = response([], code=1, _type="playlist", id=PLAYLIST_ID)
        with pytest.raises(ProviderError, match="could not be loaded"):
            await provider.playlist(PLAYLIST, limit=2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "continuation",
    [
        "",
        "not-json",
        "[]",
        '["youtube","PLdifferent123",2]',
        '["youtube_music","PLfixture012345",2]',
        '["youtube","PLfixture012345",0]',
        '["youtube","PLfixture012345",true]',
        '["youtube","PLfixture012345",-1]',
        '["youtube","PLfixture012345",2.5]',
        "x" * 513,
    ],
)
def test_playlist_continuation_is_bound_to_provider_identity_and_position(
    continuation: str,
) -> None:
    runner = FixtureRunner(response([]))
    with pytest.raises(ValueError, match="continuation"):
        asyncio.run(
            YouTubeProvider(Path("node"), runner=runner).playlist(
                PLAYLIST, limit=10, continuation=continuation
            )
        )
    assert runner.calls == []


@pytest.mark.parametrize("positions", [[], [0], [True], [3], ["1"], {}, [1, 1]])
def test_invalid_source_positions_are_not_silently_reindexed(positions: object) -> None:
    provider = YouTubeProvider(
        Path("node"),
        runner=FixtureRunner(
            response(
                [song()], _type="playlist", id=PLAYLIST_ID, requested_entries=positions
            )
        ),
    )
    with pytest.raises(ProviderError, match="positions"):
        asyncio.run(provider.playlist(PLAYLIST, limit=1))


@pytest.mark.parametrize(
    "result",
    [
        response([song()], _type="playlist", id="PLdifferent123"),
        response([song()], _type="video", id=PLAYLIST_ID),
        response(None, _type="playlist", id=PLAYLIST_ID),
        ProcessResult(0, b"not-json", b""),
    ],
)
def test_malformed_playlist_response_is_rejected(result: ProcessResult) -> None:
    provider = YouTubeProvider(Path("node"), runner=FixtureRunner(result))
    with pytest.raises(ProviderError):
        asyncio.run(provider.playlist(PLAYLIST, limit=10))


@pytest.mark.parametrize("provider_type", [YouTubeProvider, YouTubeMusicProvider])
def test_discovery_request_failures_keep_retryability_without_exposing_diagnostics(
    provider_type: type[YouTubeProvider],
) -> None:
    async def scenario() -> None:
        runner = FixtureRunner(
            ProcessResult(1, b"", b"HTTP Error 503 diagnostic-secret")
        )
        provider = provider_type(Path("node"), runner=runner)
        with pytest.raises(ProviderError) as search_error:
            await provider.search("song", limit=10)
        assert search_error.value.retryable
        assert "diagnostic-secret" not in str(search_error.value)
        with pytest.raises(ProviderError) as playlist_error:
            await provider.playlist(PLAYLIST, limit=10)
        assert playlist_error.value.retryable
        assert "diagnostic-secret" not in str(playlist_error.value)
        assert len(runner.calls) == 2

    asyncio.run(scenario())


def test_discovery_cancellation_is_targeted_and_close_settles_remaining_work() -> None:
    async def scenario() -> None:
        started: asyncio.Queue[int] = asyncio.Queue()
        cleaned: set[int] = set()
        count = 0

        async def runner(args: tuple[str, ...], *, timeout: float) -> ProcessResult:
            nonlocal count
            index = count
            count += 1
            started.put_nowait(index)
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.add(index)
            return response([])

        video = YouTubeProvider(Path("node"), runner=runner)
        music = YouTubeMusicProvider(Path("node"), runner=runner)
        tasks = [
            asyncio.create_task(video.playlist(PLAYLIST, limit=10)),
            asyncio.create_task(video.search("song", limit=10)),
            asyncio.create_task(music.search("song", limit=10)),
        ]
        for _ in tasks:
            await started.get()
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert cleaned == {0}
        assert not tasks[1].done() and not tasks[2].done()
        await asyncio.gather(video.close(), music.close())
        assert cleaned == {0, 1, 2}
        for task in tasks:
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(ProviderError, match="closed"):
            await video.playlist(PLAYLIST, limit=10)
        with pytest.raises(ProviderError, match="closed"):
            await music.search("song", limit=10)
        assert not asyncio.all_tasks() - {asyncio.current_task()}

    asyncio.run(scenario())
