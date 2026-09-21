# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
from pathlib import Path

import pytest

from nahormaar_backend.engine.domain.catalog import MediaKind
from nahormaar_backend.engine.domain.tracks import MediaIdentity
from nahormaar_backend.engine.providers import (
    PlaybackProvider,
    PlaylistProvider,
    ProviderError,
    RadioProvider,
    SearchProvider,
    TrackProvider,
)
from nahormaar_backend.engine.youtube import YouTubeProvider
from nahormaar_backend.integrations.processes import (
    ProcessCleanupError,
    ProcessOutputLimitError,
    ProcessResult,
    ProcessTimeoutError,
)

IDENTITY = MediaIdentity("youtube", "GCYGuZGE6DA")
URL = "https://www.youtube.com/watch?v=GCYGuZGE6DA"
PLAYLIST_ID = "PLfixture012345"


def details(**changes: object) -> ProcessResult:
    value: dict[str, object] = {
        "id": IDENTITY.external_id,
        "title": " Я хочу любить ",
        "artist": " Artist, with comma ",
        "uploader": " Uploader ",
        "channel_id": "UCUploader",
        "channel_url": " https://www.youtube.com/channel/UCUploader ",
        "duration": 180,
        "thumbnail": " https://i.ytimg.com/cover.jpg ",
        "url": "https://stream.example/audio?signature=temporary-secret",
        "http_headers": {"User-Agent": "test", "Authorization": "ephemeral-header"},
        "acodec": "opus",
    }
    value.update(changes)
    return ProcessResult(0, json.dumps(value).encode(), b"")


class FixtureRunner:
    def __init__(self, result: ProcessResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float]] = []

    async def __call__(self, args: tuple[str, ...], *, timeout: float) -> ProcessResult:
        self.calls.append((args, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    "url",
    [
        URL,
        "https://youtube.com/watch?v=GCYGuZGE6DA&t=20",
        "https://m.youtube.com/watch?v=GCYGuZGE6DA",
        "https://music.youtube.com/watch?v=GCYGuZGE6DA&list=PLfixture012345",
        "https://youtu.be/GCYGuZGE6DA?si=unrelated",
        "http://youtu.be/GCYGuZGE6DA/",
        "https://www.youtube.com/embed/GCYGuZGE6DA",
        "https://www.youtube.com/shorts/GCYGuZGE6DA",
        "  https://www.youtube.com/watch?v=GCYGuZGE6DA  ",
    ],
)
def test_aliases_have_one_identity_without_io(url: str) -> None:
    runner = FixtureRunner(details())
    provider = YouTubeProvider(Path("node"), runner=runner)
    reference = provider.identify(url)
    assert reference is not None
    assert reference.identity == IDENTITY
    assert reference.kind is MediaKind.TRACK
    assert "list=" not in reference.source_url
    assert "si=" not in reference.source_url
    assert runner.calls == []


def test_mixed_links_choose_track_unless_playlist_was_requested() -> None:
    provider = YouTubeProvider(Path("node"), runner=FixtureRunner(details()))
    mixed = URL + "&list=" + PLAYLIST_ID
    track = provider.identify(mixed)
    playlist = provider.identify(mixed, kind=MediaKind.PLAYLIST)
    assert track is not None and track.kind is MediaKind.TRACK
    assert playlist is not None
    assert playlist.identity == MediaIdentity("youtube", PLAYLIST_ID)
    assert playlist.kind is MediaKind.PLAYLIST
    assert provider.identify(playlist.source_url) == playlist
    assert provider.identify(playlist.source_url, kind=MediaKind.TRACK) is None
    assert provider.identify(URL, kind=MediaKind.PLAYLIST) is None
    assert isinstance(provider, TrackProvider)
    assert isinstance(provider, PlaybackProvider)
    assert isinstance(provider, SearchProvider)
    assert isinstance(provider, PlaylistProvider)
    assert not isinstance(provider, RadioProvider)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "title or artist",
        "https://example.com/watch?v=GCYGuZGE6DA",
        "file:///watch?v=GCYGuZGE6DA",
        "https://youtube.com.example/watch?v=GCYGuZGE6DA",
        "https://user@youtube.com/watch?v=GCYGuZGE6DA",
        "https://youtube.com:bad/watch?v=GCYGuZGE6DA",
        "https://youtube.com:123/watch?v=GCYGuZGE6DA",
        "https://youtu.be/GCYGuZGE6DA/extra",
        "https://youtu.be/short",
        URL + "&v=GCYGuZGE6DA",
        URL + "&v=",
        "https://youtube.com/watch?v=GCYGuZGE6D%2F",
        "https://youtube.com/other?list=PLfixture012345",
        "https://youtube.com/playlist?list=PLfixture012345&list=PLfixture012345",
        "https://youtube.com/playlist?list=short",
        "https://[invalid",
        URL + "\n",
        URL + "&padding=" + "a" * 2048,
    ],
)
def test_unsupported_links_do_not_identify(url: str) -> None:
    provider = YouTubeProvider(Path("node"), runner=FixtureRunner(details()))
    assert provider.identify(url) is None


def test_details_and_audio_normalize_metadata_and_use_one_request_each() -> None:
    async def scenario() -> None:
        runner = FixtureRunner(details(track=" Song "))
        provider = YouTubeProvider(Path("runtime/node"), timeout=7, runner=runner)
        finding = await provider.track(IDENTITY)
        assert finding.metadata.title == "Song"
        assert finding.metadata.artist == "Artist, with comma"
        assert finding.metadata.uploader == "Uploader"
        assert (
            finding.metadata.uploader_url
            == "https://www.youtube.com/channel/UCUploader"
        )
        assert finding.metadata.thumbnail_url == "https://i.ytimg.com/cover.jpg"
        assert finding.metadata.duration_seconds == 180
        assert finding.artists is None  # An uploader is not an identified artist.
        assert finding.reference.identity == IDENTITY
        assert len(runner.calls) == 1
        playable = await provider.resolve_audio(IDENTITY)
        assert playable.track == finding and playable.is_opus
        assert "temporary-secret" in playable.stream_url
        assert playable.headers == (
            ("User-Agent", "test"),
            ("Authorization", "ephemeral-header"),
        )
        assert "temporary-secret" not in repr(playable)
        assert "ephemeral-header" not in repr(playable)
        assert len(runner.calls) == 2
        args, timeout = runner.calls[0]
        assert timeout == 7
        assert args[-2:] == ("--", URL)
        assert args[args.index("--js-runtimes") + 1] == f"node:{Path('runtime/node')}"
        assert args[args.index("--format") + 1] == "bestaudio/best"
        for option in ("--retries", "--extractor-retries", "--fragment-retries"):
            assert args[args.index(option) + 1] == "0"
        for option in (
            "--ignore-config",
            "--no-cache-dir",
            "--no-plugin-dirs",
            "--no-remote-components",
            "--simulate",
            "--no-playlist",
            "--dump-single-json",
        ):
            assert option in args
        await provider.close()
        await provider.close()
        with pytest.raises(ProviderError, match="closed"):
            await provider.track(IDENTITY)
        assert len(runner.calls) == 2

    asyncio.run(scenario())


def test_sparse_metadata_keeps_unicode_and_uses_known_fallback_fields() -> None:
    async def scenario() -> None:
        runner = FixtureRunner(
            details(
                track=" ",
                artist=[],
                uploader=" ",
                channel=" Кино ",
                thumbnail=None,
                thumbnails=[None, {"url": "small"}, {"url": " large "}, {}],
                channel_url=None,
                uploader_url=" https://youtube.com/@channel ",
                acodec="aac",
                http_headers=None,
            )
        )
        playable = await YouTubeProvider(Path("node"), runner=runner).resolve_audio(
            IDENTITY
        )
        assert playable.track.metadata.title == "Я хочу любить"
        assert playable.track.metadata.artist is None
        assert playable.track.metadata.uploader == "Кино"
        assert playable.track.metadata.thumbnail_url == "large"
        assert playable.track.metadata.uploader_url == "https://youtube.com/@channel"
        assert not playable.is_opus and playable.headers == ()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "duration", [None, True, 0, -1, "180", 10**400, float("inf"), float("nan")]
)
def test_unknown_duration_can_be_cataloged_but_not_played(duration: object) -> None:
    async def scenario() -> None:
        provider = YouTubeProvider(
            Path("node"), runner=FixtureRunner(details(duration=duration))
        )
        assert (await provider.track(IDENTITY)).metadata.duration_seconds is None
        with pytest.raises(ProviderError, match="finite track duration"):
            await provider.resolve_audio(IDENTITY)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ProcessResult(0, b"not json", b""), "invalid track metadata"),
        (ProcessResult(0, b"\xff", b""), "invalid track metadata"),
        (ProcessResult(0, b"[]", b""), "invalid track metadata"),
        (details(_type="playlist"), "single YouTube tracks"),
        (details(id="other"), "different track identity"),
        (details(id=None), "different track identity"),
        (details(is_live=True), "livestreams"),
        (details(live_status="is_upcoming"), "livestreams"),
        (details(availability="private"), "unavailable"),
    ],
)
def test_unusable_detail_responses_have_typed_errors(
    result: ProcessResult, message: str
) -> None:
    provider = YouTubeProvider(Path("node"), runner=FixtureRunner(result))
    with pytest.raises(ProviderError, match=message):
        asyncio.run(provider.track(IDENTITY))


@pytest.mark.parametrize(
    "url",
    [
        None,
        4,
        "",
        "file:///audio",
        "https://user:pass@stream.example/a",
        "https://[bad",
        "https://stream.example:bad/a",
    ],
)
def test_invalid_audio_urls_are_not_playable(url: object) -> None:
    provider = YouTubeProvider(Path("node"), runner=FixtureRunner(details(url=url)))
    with pytest.raises(ProviderError, match="no playable audio stream"):
        asyncio.run(provider.resolve_audio(IDENTITY))


@pytest.mark.parametrize(
    "headers",
    [[], {"": "value"}, {"name": 1}, {"name\r\n": "value"}, {"name": "value\r\n"}],
)
def test_invalid_stream_headers_are_rejected(headers: object) -> None:
    provider = YouTubeProvider(
        Path("node"), runner=FixtureRunner(details(http_headers=headers))
    )
    with pytest.raises(ProviderError, match="invalid stream headers"):
        asyncio.run(provider.resolve_audio(IDENTITY))


@pytest.mark.parametrize(
    ("result", "retryable"),
    [
        (ProcessResult(1, b"", b"HTTP Error 503 temporary-secret"), True),
        (
            ProcessResult(
                1, b"", b"Private video; unable to download webpage temporary-secret"
            ),
            False,
        ),
        (ProcessResult(1, b"", b"extractor changed temporary-secret"), False),
        (
            ProcessResult(2, b"", b"configuration; HTTP Error 503 temporary-secret"),
            False,
        ),
        (ProcessTimeoutError("temporary-secret"), True),
        (ProcessOutputLimitError("temporary-secret"), False),
        (ProcessCleanupError("temporary-secret"), False),
        (OSError("temporary-secret"), False),
    ],
)
def test_request_failures_are_classified_without_raw_output(
    result: ProcessResult | Exception, retryable: bool
) -> None:
    runner = FixtureRunner(result)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(YouTubeProvider(Path("node"), runner=runner).track(IDENTITY))
    assert caught.value.retryable is retryable
    assert "temporary-secret" not in str(caught.value)
    assert len(runner.calls) == 1  # No adapter retry loop.


@pytest.mark.parametrize(
    "identity",
    [MediaIdentity("spotify", IDENTITY.external_id), MediaIdentity("youtube", "bad")],
)
def test_invalid_identity_fails_before_request(identity: MediaIdentity) -> None:
    runner = FixtureRunner(details())
    with pytest.raises(ProviderError, match="track identities"):
        asyncio.run(YouTubeProvider(Path("node"), runner=runner).track(identity))
    assert runner.calls == []


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        YouTubeProvider(Path("node"), timeout=timeout)


def test_cancellation_is_targeted_and_close_waits_for_all_request_cleanup() -> None:
    async def scenario() -> None:
        started = [asyncio.Event(), asyncio.Event()]
        cleaned = [asyncio.Event(), asyncio.Event()]
        call_count = 0

        async def runner(args: tuple[str, ...], *, timeout: float) -> ProcessResult:
            nonlocal call_count
            index = call_count
            call_count += 1
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned[index].set()
            return details()

        provider = YouTubeProvider(Path("node"), runner=runner)
        first = asyncio.create_task(provider.track(IDENTITY))
        second = asyncio.create_task(provider.resolve_audio(IDENTITY))
        await asyncio.gather(*(event.wait() for event in started))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert cleaned[0].is_set()
        assert not second.done() and not cleaned[1].is_set()
        await provider.close()
        assert cleaned[1].is_set()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert call_count == 2
        assert not (asyncio.all_tasks() - {asyncio.current_task()})

    asyncio.run(scenario())


def test_close_surfaces_cleanup_failure() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def runner(args: tuple[str, ...], *, timeout: float) -> ProcessResult:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise ProcessCleanupError("temporary-secret") from None
            return details()

        provider = YouTubeProvider(Path("node"), runner=runner)
        task = asyncio.create_task(provider.track(IDENTITY))
        await started.wait()
        with pytest.raises(ProviderError, match="cleaned up"):
            await provider.close()
        with pytest.raises(ProviderError, match="cleaned up"):
            await task

    asyncio.run(scenario())


def test_close_does_not_relabel_an_already_finished_request_failure() -> None:
    async def scenario() -> None:
        finished = asyncio.Event()

        async def runner(args: tuple[str, ...], *, timeout: float) -> ProcessResult:
            finished.set()
            raise ProcessTimeoutError("request already cleaned up")

        provider = YouTubeProvider(Path("node"), runner=runner)
        task = asyncio.create_task(provider.track(IDENTITY))
        await finished.wait()
        # The runner has finished, but its awaiting caller has not resumed yet.
        await provider.close()
        with pytest.raises(ProviderError, match="too long") as caught:
            await task
        assert caught.value.retryable

    asyncio.run(scenario())
