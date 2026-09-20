# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

import nahormaar_backend.integrations.youtube as youtube_module
from nahormaar_backend.application.audio import TrackError
from nahormaar_backend.integrations.processes import (
    ProcessResult,
    ProcessTimeoutError,
    run_process,
)
from nahormaar_backend.integrations.youtube import YouTubeResolver

_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _metadata(**changes: object) -> bytes:
    value: dict[str, object] = {
        "id": "dQw4w9WgXcQ",
        "duration": 213.0,
        "title": "Example song",
        "uploader": "Example artist",
        "live_status": "not_live",
        "url": "https://stream.example/audio?signature=secret",
        "http_headers": {"User-Agent": "test", "Referer": "https://youtube.com/"},
    }
    value.update(changes)
    return json.dumps(value).encode()


def test_resolver_returns_stream_and_headers_without_downloading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: tuple[str, ...] | None = None

    async def fake_run_process(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> ProcessResult:
        nonlocal captured
        del max_output_bytes
        captured = tuple(os.fspath(arg) for arg in args)
        assert timeout == 7
        return ProcessResult(0, _metadata(), b"")

    monkeypatch.setattr(youtube_module, "run_process", fake_run_process)
    node_path = tmp_path / "node.exe"
    resolved = asyncio.run(YouTubeResolver(node_path, timeout=7).resolve(_VIDEO_URL))

    assert captured is not None
    assert resolved.stream_url == "https://stream.example/audio?signature=secret"
    assert resolved.headers == (
        ("User-Agent", "test"),
        ("Referer", "https://youtube.com/"),
    )
    assert not resolved.is_opus
    assert resolved.title == "Example song"
    assert resolved.uploader == "Example artist"
    assert captured[-1] == _VIDEO_URL
    assert captured[captured.index("--js-runtimes") + 1] == f"node:{node_path}"
    assert "--no-playlist" in captured
    assert "--ignore-config" in captured
    assert "--no-cache-dir" in captured
    assert "--no-plugin-dirs" in captured
    assert "--no-remote-components" in captured
    assert "--no-js-runtimes" in captured
    assert captured[captured.index("--format") + 1] == "bestaudio/best"
    for option in ("--extractor-retries", "--retries", "--fragment-retries"):
        assert captured[captured.index(option) + 1] == "0"
    assert captured[captured.index("--color") + 1] == "never"
    assert "--dump-single-json" in captured
    assert "--simulate" in captured
    assert captured[-2] == "--"


@pytest.mark.parametrize("missing", [None, 42])
def test_missing_or_invalid_display_metadata_does_not_prevent_playback(
    missing: object,
) -> None:
    resolved = youtube_module._resolved_track(  # pyright: ignore[reportPrivateUsage]
        ProcessResult(0, _metadata(title=missing, uploader=missing), b"")
    )
    assert resolved.title is None
    assert resolved.uploader is None
    assert resolved.stream_url


def test_music_metadata_prefers_track_name_and_preserves_artist_and_channel() -> None:
    resolved = youtube_module._resolved_track(  # pyright: ignore[reportPrivateUsage]
        ProcessResult(
            0,
            _metadata(
                track="Song",
                artist="Artist",
                thumbnail="https://i.ytimg.com/cover.jpg",
                channel_url="https://www.youtube.com/channel/example",
            ),
            b"",
        )
    )
    assert resolved.metadata.title == "Song"
    assert resolved.metadata.artist == "Artist"
    assert resolved.metadata.uploader == "Example artist"
    assert resolved.metadata.duration_seconds == 213
    assert resolved.metadata.video_id == "dQw4w9WgXcQ"
    assert resolved.metadata.thumbnail_url == "https://i.ytimg.com/cover.jpg"
    assert resolved.metadata.uploader_url == "https://www.youtube.com/channel/example"


@pytest.mark.parametrize(
    ("acodec", "is_opus"),
    [
        ("opus", True),
        ("aac", False),
        (None, False),
    ],
)
def test_resolver_marks_only_selected_opus_audio(
    monkeypatch: pytest.MonkeyPatch, acodec: object, is_opus: bool
) -> None:
    async def fake_run_process(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> ProcessResult:
        del args, timeout, max_output_bytes
        return ProcessResult(0, _metadata(acodec=acodec), b"")

    monkeypatch.setattr(youtube_module, "run_process", fake_run_process)
    resolved = asyncio.run(YouTubeResolver(Path("node")).resolve(_VIDEO_URL))

    assert resolved.is_opus is is_opus


@pytest.mark.parametrize(
    "source_url",
    [
        "cats",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://youtu.be/not-an-id",
    ],
)
def test_resolver_rejects_unsupported_sources_before_starting_process(
    monkeypatch: pytest.MonkeyPatch, source_url: str
) -> None:
    async def unexpected_process(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> ProcessResult:
        del args, timeout, max_output_bytes
        raise AssertionError("process must not start")

    monkeypatch.setattr(youtube_module, "run_process", unexpected_process)
    with pytest.raises(TrackError, match="single YouTube video URLs"):
        asyncio.run(YouTubeResolver(Path("node")).resolve(source_url))


@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(duration=None),
        _metadata(is_live=True),
        _metadata(live_status="was_live"),
    ],
)
def test_resolver_rejects_non_finite_and_live_media(
    monkeypatch: pytest.MonkeyPatch, metadata: bytes
) -> None:
    async def fake_run_process(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> ProcessResult:
        del args, timeout, max_output_bytes
        return ProcessResult(0, metadata, b"")

    monkeypatch.setattr(youtube_module, "run_process", fake_run_process)
    with pytest.raises(TrackError, match="livestreams"):
        asyncio.run(YouTubeResolver(Path("node")).resolve(_VIDEO_URL))


@pytest.mark.parametrize(
    ("stderr", "retryable"),
    [
        (b"ERROR: HTTP Error 503: Service Unavailable", True),
        (b"ERROR: Private video https://signed.example/secret", False),
        (b"ERROR: extractor changed https://signed.example/secret", False),
    ],
)
def test_resolver_normalizes_extraction_failures_without_leaking_output(
    monkeypatch: pytest.MonkeyPatch, stderr: bytes, retryable: bool
) -> None:
    async def fake_run_process(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> ProcessResult:
        del args, timeout, max_output_bytes
        return ProcessResult(1, b"", stderr)

    monkeypatch.setattr(youtube_module, "run_process", fake_run_process)
    with pytest.raises(TrackError) as caught:
        asyncio.run(YouTubeResolver(Path("node")).resolve(_VIDEO_URL))
    assert caught.value.retryable is retryable
    assert "signed.example" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_resolver_marks_timeout_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_process(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> ProcessResult:
        del args, timeout, max_output_bytes
        raise ProcessTimeoutError("contains https://signed.example/secret")

    monkeypatch.setattr(youtube_module, "run_process", fake_run_process)
    with pytest.raises(TrackError, match="too long") as caught:
        asyncio.run(YouTubeResolver(Path("node")).resolve(_VIDEO_URL))
    assert caught.value.retryable
    assert "signed.example" not in str(caught.value)


def test_installed_ytdlp_accepts_the_resolver_arguments_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check_arguments(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> ProcessResult:
        command = [os.fspath(arg) for arg in args]
        command.insert(command.index("--"), "--list-extractors")
        result = await run_process(
            command, timeout=timeout, max_output_bytes=max_output_bytes
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        return ProcessResult(0, _metadata(), b"")

    monkeypatch.setattr(youtube_module, "run_process", check_arguments)
    asyncio.run(YouTubeResolver(Path("node")).resolve(_VIDEO_URL))


def test_invalid_resolver_configuration_is_an_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rejected_arguments(
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
    ) -> ProcessResult:
        del args, timeout
        return ProcessResult(2, b"", b"unknown option")

    monkeypatch.setattr(youtube_module, "run_process", rejected_arguments)
    with pytest.raises(RuntimeError, match="configuration") as error:
        asyncio.run(YouTubeResolver(Path("node")).resolve(_VIDEO_URL))
    assert not isinstance(error.value, TrackError)
