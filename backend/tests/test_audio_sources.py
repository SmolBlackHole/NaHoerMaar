# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false
import threading
import time
from collections import deque
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nahormaar_backend.integrations.audio_sources import FFmpegSource, _ffmpeg_arguments


def source_state(
    *,
    diagnostics: tuple[str, ...] = (),
    duration_seconds: float | None = None,
    position_seconds: float = 0,
    output_seconds: float = 0,
) -> FFmpegSource:
    def wait(*, timeout: float) -> int:
        del timeout
        return 0

    def poll() -> int:
        return 0

    def join(timeout: float) -> None:
        del timeout

    source = object.__new__(FFmpegSource)
    source._diagnostics = deque(diagnostics, maxlen=8)
    source._diagnostics_lock = threading.Lock()
    source._duration_seconds = duration_seconds
    source._position_seconds = position_seconds
    source._output_seconds = output_seconds
    source._stdout = BytesIO()
    source._process = cast(
        Any,
        SimpleNamespace(wait=wait, poll=poll),
    )
    source._stderr_thread = cast(Any, SimpleNamespace(join=join))
    source._packets = None
    source._current_error = None
    source._cleanup_lock = threading.Lock()
    source._closed = threading.Event()
    source._pid = 123
    source._started_at = time.monotonic()
    return source


@pytest.mark.parametrize(
    "diagnostic",
    [
        "http_403",
        "timeout",
        "connection_reset",
        "demux_error",
        "decode_error",
        "interrupted",
        "ffmpeg_error",
    ],
)
def test_terminal_ffmpeg_diagnostics_fail_even_with_zero_exit(
    diagnostic: str,
) -> None:
    source = source_state(diagnostics=(diagnostic,))

    assert source._terminal_diagnostic()
    assert source.read() == b""
    assert source.current_error is not None


def test_generic_ffmpeg_warning_is_not_a_terminal_error() -> None:
    source = source_state(diagnostics=("ffmpeg_message",))

    assert not source._terminal_diagnostic()
    assert source.read() == b""
    assert source.current_error is None


def test_known_duration_detects_interrupted_output_with_seek_and_tolerance() -> None:
    interrupted = source_state(
        duration_seconds=111,
        position_seconds=45,
        output_seconds=20,
    )
    within_tolerance = source_state(
        duration_seconds=111,
        position_seconds=45,
        output_seconds=65.1,
    )

    assert interrupted._ended_early()
    assert not within_tolerance._ended_early()


def test_unknown_duration_alone_is_not_a_failure() -> None:
    source = source_state(duration_seconds=None, output_seconds=0)

    assert not source._ended_early()
    assert source.read() == b""
    assert source.current_error is None


def test_ffmpeg_header_arguments_are_discrete_and_reject_injection() -> None:
    arguments = _ffmpeg_arguments(
        Path("ffmpeg.exe"),
        "https://example.invalid/audio?sig=secret",
        (("User-Agent", "NaHoerMaar"), ("Cookie", "token=secret")),
    )
    assert arguments[0] == "ffmpeg.exe"
    assert arguments[arguments.index("-headers") + 1] == (
        "User-Agent: NaHoerMaar\r\nCookie: token=secret\r\n"
    )
    assert arguments[arguments.index("-rw_timeout") + 1] == "15000000"
    assert "-reconnect" not in arguments
    with pytest.raises(ValueError, match="invalid HTTP headers"):
        _ffmpeg_arguments(
            Path("ffmpeg.exe"),
            "https://example.invalid/audio",
            (("Authorization", "safe\r\nInjected: value"),),
        )
