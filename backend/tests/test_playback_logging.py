# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend.application.audio import TrackError
from nahormaar_backend.config import ffmpeg_executable
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.models import QueueEntry
from nahormaar_backend.integrations.audio_sources import FFmpegSource, _diagnostic_kind
from test_playback import _controller, _wait_until

# pyright: reportPrivateUsage=false


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (b"HTTP error 403 Forbidden https://private.invalid/?token=secret", "http_403"),
        (b"Server returned 503 Service Unavailable", "http_503"),
        (b"Connection timed out: https://private.invalid/secret", "timeout"),
        (b"Connection reset by peer", "connection_reset"),
        (b"Invalid data found when processing input", "invalid_media"),
        (b"Authorization: Bearer secret", "ffmpeg_message"),
    ],
)
def test_ffmpeg_diagnostics_keep_categories_only(message: bytes, expected: str) -> None:
    assert _diagnostic_kind(message) == expected


def test_ffmpeg_failure_logs_exit_without_source_or_headers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nahormaar_backend")
    source = FFmpegSource(
        ffmpeg_executable(), str(tmp_path / "private-missing-source.wav"), ()
    )
    try:
        assert source.read() == b""
        assert source.current_error is not None
    finally:
        source.cleanup()
    assert "ffmpeg.eof" in caplog.text
    assert f"audio_pid={source.process_id}" in caplog.text
    assert "diagnostics=ffmpeg_message" in caplog.text
    assert "killed=False" in caplog.text
    assert "private-missing-source" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_logs_distinguish_stream_retry_from_explicit_skip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nahormaar_backend")
    request_id, actor_id = uuid4(), uuid4()

    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        try:
            await controller.enqueue(QueueEntry("fixture:first"))
            await controller.enqueue(QueueEntry("fixture:second"))
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0, "do-not-log-stream-url")
            await _wait_until(lambda: len(voice.played) == 1)
            voice.position_seconds = 42.5
            voice.complete(0, TrackError("do-not-log-secret-error", retryable=True))
            await _wait_until(lambda: len(resolver.requests) == 2)
            resolver.succeed(1)
            await _wait_until(lambda: len(voice.played) == 2)
            await controller.request(
                request_id,
                commands.Control("skip", controller.status.attempt_id),
                actor_id=actor_id,
            )
        finally:
            await controller.close()

    asyncio.run(scenario())
    assert "playback.completed" in caplog.text
    assert "position=42.5" in caplog.text
    assert "error=TrackError" in caplog.text
    assert "retryable=True action=retry" in caplog.text
    assert f"request={request_id} actor={actor_id} command=skip" in caplog.text
    assert "playback.skip" in caplog.text
    assert "do-not-log" not in caplog.text
    assert "https://" not in caplog.text
