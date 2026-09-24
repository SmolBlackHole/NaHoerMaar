# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false
import audioop
import logging
import struct
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from nahormaar_backend.config import ffmpeg_executable
from nahormaar_backend.integrations import audio_mixer
from nahormaar_backend.integrations.audio_mixer import (
    BUFFER_FRAMES,
    BufferedAudio,
    CrossfadeSource,
)
from nahormaar_backend.integrations.audio_sources import (
    AudioFrame,
    FrameEncoder,
    FFmpegSource,
    VolumeSource,
)
from test_audio_quality import _opus_fixture, _packets


class Frames:
    def __init__(self, value: int, count: int) -> None:
        self.frame = AudioFrame(struct.pack("<hh", value, value) * 960)
        self.remaining = count
        self.original = self
        self.current_error: Exception | None = None
        self.closed = False

    def read_frame(self) -> AudioFrame | None:
        if self.remaining:
            self.remaining -= 1
            return self.frame
        return None

    def cleanup(self) -> None:
        self.closed = True


class PCMEncoder:
    def encode(self, frame: AudioFrame, volume: float) -> bytes:
        return audioop.mul(frame.pcm, 2, volume)


def buffered(value: int, frames: int) -> tuple[Frames, BufferedAudio]:
    source = Frames(value, frames)
    audio = BufferedAudio(cast(VolumeSource, source))
    assert audio.wait_ready(min(frames, BUFFER_FRAMES))
    return source, audio


def test_buffer_logs_first_frame_and_slow_source_read(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowFrames(Frames):
        def read_frame(self) -> AudioFrame | None:
            time.sleep(0.02)
            return super().read_frame()

    monkeypatch.setattr(audio_mixer, "_SLOW_READ_SECONDS", 0.005)
    source = SlowFrames(100, 1)
    with caplog.at_level(logging.INFO):
        audio = BufferedAudio(cast(VolumeSource, source))
        try:
            assert audio.wait_ready(1)
            audio._thread.join(timeout=1)
            assert not audio._thread.is_alive()
        finally:
            audio.cleanup()

    assert "audio.buffer.first_frame" in caplog.text
    assert "audio.buffer.read_slow" in caplog.text
    assert audio.reader_wait_seconds is None


def test_complementary_gains_keep_peak_and_fade_on_frame_clock() -> None:
    first, current = buffered(30000, 100)
    second, upcoming = buffered(-30000, 100)
    mixer = CrossfadeSource(current, volume=1)
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    due = threading.Event()
    done = threading.Event()
    try:
        assert mixer.stage(upcoming, seconds=1, duration=2, on_due=due.set)
        before = [mixer.read() for _ in range(50)]
        assert not due.is_set()
        assert all(frame == first.frame.pcm for frame in before)
        mixer.read()
        assert due.is_set()
        assert mixer.activate(done.set)
        frames = [mixer.read() for _ in range(50)]
        values = [struct.unpack_from("<h", frame)[0] for frame in frames]
        assert values[0] > 29000
        assert values[-1] == -30000
        assert values == sorted(values, reverse=True)
        assert max(audioop.max(frame, 2) for frame in frames) <= 30000
        assert done.wait(1)
        assert first.closed
        assert not second.closed
        assert mixer.read() == second.frame.pcm
    finally:
        mixer.cleanup()
    assert second.closed


def test_mix_applies_live_volume_to_both_tracks_once() -> None:
    _, current = buffered(30000, 50)
    _, upcoming = buffered(30000, 50)
    mixer = CrossfadeSource(current, volume=0.4)
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    try:
        mixer.stage(upcoming, seconds=0.4, duration=0.4, on_due=lambda: None)
        mixer.read()
        assert mixer.activate(lambda: None)
        mixed = mixer.read()
        assert 11998 <= audioop.max(mixed, 2) <= 12000
        mixer.volume = 0
        assert audioop.max(mixer.read(), 2) == 0
    finally:
        mixer.cleanup()


def test_paused_start_and_pause_freeze_the_audio_position() -> None:
    first, current = buffered(12000, 10)
    mixer = CrossfadeSource(current, volume=1, position=42.36, paused=True)
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    try:
        for _ in range(3):
            assert audioop.max(mixer.read(), 2) == 0
        assert mixer.position_seconds == 42.36
        mixer.resume()
        assert mixer.read() == first.frame.pcm
        assert mixer.position_seconds == pytest.approx(42.38)
        mixer.pause()
        assert audioop.max(mixer.read(), 2) == 0
        assert mixer.position_seconds == pytest.approx(42.38)
        mixer.resume()
        assert mixer.read() == first.frame.pcm
        assert mixer.position_seconds == pytest.approx(42.4)
    finally:
        mixer.cleanup()


def test_start_callback_waits_for_first_encoded_media_frame() -> None:
    first, current = buffered(12000, 10)
    started: list[float] = []
    mixer = CrossfadeSource(
        current,
        volume=1,
        position=42.36,
        paused=True,
        on_started=lambda: started.append(mixer.position_seconds),
    )
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    try:
        assert audioop.max(mixer.read(), 2) == 0
        assert started == []
        mixer.resume()
        assert mixer.read() == first.frame.pcm
        assert started == [pytest.approx(42.38)]
        assert mixer.read() == first.frame.pcm
        assert len(started) == 1
        assert not mixer.paused
        assert mixer.current_error is None
    finally:
        mixer.cleanup()


def test_underrun_silence_does_not_confirm_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, current = buffered(12000, 10)
    started = threading.Event()
    mixer = CrossfadeSource(current, volume=1, on_started=started.set)
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    take = current.take
    monkeypatch.setattr(current, "take", lambda timeout=0.02: None)
    try:
        assert audioop.max(mixer.read(), 2) == 0
        assert not started.is_set()
        assert mixer.diagnostics.underrun_count == 1
        time.sleep(0.01)
        monkeypatch.setattr(current, "take", take)
        assert mixer.read()
        assert started.is_set()
        diagnostics = mixer.diagnostics
        assert diagnostics.underrun_count == 1
        assert diagnostics.stalled_seconds >= 0.01
        assert diagnostics.max_stall_seconds >= 0.01
        assert diagnostics.first_frame_seconds is not None
    finally:
        mixer.cleanup()


def test_activation_replaces_start_callback_before_incoming_first_frame() -> None:
    _, current = buffered(30000, 100)
    _, upcoming = buffered(-30000, 100)
    starts: list[str] = []
    mixer = CrossfadeSource(current, volume=1, on_started=lambda: starts.append("old"))
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    try:
        mixer.stage(upcoming, seconds=1, duration=1, on_due=lambda: None)
        mixer.read()
        assert starts == ["old"]
        assert mixer.activate(
            lambda: None,
            on_started=lambda: starts.append("incoming"),
        )
        assert starts == ["old"]
        mixer.read()
        assert starts == ["old", "incoming"]
        mixer.read()
        assert starts == ["old", "incoming"]
    finally:
        mixer.cleanup()


def test_temporary_tail_underrun_does_not_end_fade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, current = buffered(30000, 100)
    _, upcoming = buffered(-30000, 100)
    mixer = CrossfadeSource(current, volume=1)
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    take = current.take
    missing = False

    def briefly_empty(timeout: float = 0.02) -> AudioFrame | None:
        return None if missing else take(timeout)

    monkeypatch.setattr(current, "take", briefly_empty)
    done = threading.Event()
    try:
        mixer.stage(upcoming, seconds=1, duration=1, on_due=lambda: None)
        mixer.read()
        assert mixer.activate(done.set)
        mixer.read()
        missing = True
        frame = mixer.read()
        # An empty buffer is not EOF. Keep the envelope instead of abruptly
        # sending the incoming track at full volume and killing the tail.
        assert audioop.max(frame, 2) < 200
        assert mixer.transitioning
        assert not first.closed
        missing = False
        for _ in range(48):
            mixer.read()
        assert done.wait(1)
        assert first.closed
    finally:
        mixer.cleanup()


@pytest.mark.parametrize("failed", [False, True])
def test_early_tail_end_keeps_incoming_fade_envelope(failed: bool) -> None:
    first, current = buffered(30000, 2)
    _, upcoming = buffered(-30000, 100)
    if failed:
        current._error = OSError("source failed")
    mixer = CrossfadeSource(current, volume=1)
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    done = threading.Event()
    try:
        mixer.stage(upcoming, seconds=1, duration=1, on_due=lambda: None)
        mixer.read()
        assert mixer.activate(done.set)
        mixer.read()
        values = [struct.unpack_from("<h", mixer.read())[0] for _ in range(49)]
        assert -200 < values[0] < 0
        assert values[-1] == -30000
        assert values == sorted(values, reverse=True)
        assert done.wait(1)
        assert first.closed
    finally:
        mixer.cleanup()


def test_outgoing_retirement_failure_remains_the_mixer_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, current = buffered(30000, 100)
    _, upcoming = buffered(-30000, 100)
    mixer = CrossfadeSource(current, volume=1)
    mixer._encoder = cast(FrameEncoder, PCMEncoder())
    cleanup = current.cleanup
    attempts = 0

    def fail_once() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Outgoing cleanup failed.")
        cleanup()

    monkeypatch.setattr(current, "cleanup", fail_once)
    faded = threading.Event()
    try:
        mixer.stage(upcoming, seconds=0.02, duration=0.02, on_due=lambda: None)
        mixer.read()
        assert mixer.activate(faded.set)
        mixer.read()
        assert faded.wait(1)
        assert isinstance(mixer.current_error, RuntimeError)
        assert mixer.read() == b""
    finally:
        mixer.cleanup()
    assert first.closed


def test_real_opus_packets_survive_before_and_after_overlap(tmp_path: Path) -> None:
    path = _opus_fixture(tmp_path)
    packets = _packets(path)
    current = BufferedAudio(
        VolumeSource(
            FFmpegSource(
                ffmpeg_executable(),
                str(path),
                (),
                opus=True,
            )
        )
    )
    upcoming = BufferedAudio(
        VolumeSource(
            FFmpegSource(
                ffmpeg_executable(),
                str(path),
                (),
                opus=True,
            )
        )
    )
    mixer = CrossfadeSource(current, volume=1)
    done = threading.Event()
    due = threading.Event()
    try:
        assert current.wait_ready(len(packets))
        assert upcoming.wait_ready(len(packets))
        mixer.stage(upcoming, seconds=0.4, duration=1, on_due=due.set)
        prefix: list[bytes] = []
        while not due.is_set():
            prefix.append(mixer.read())
        assert prefix == packets[: len(prefix)]
        assert mixer._encoder._encoder is None
        assert mixer.activate(done.set)
        overlap = [mixer.read() for _ in range(20)]
        assert all(overlap)
        assert mixer._encoder._encoder is not None
        assert done.wait(1)
        rest = list(iter(mixer.read, b""))
        assert rest == packets[20:]
    finally:
        mixer.cleanup()
    assert current.source.original.closed and upcoming.source.original.closed


def test_buffer_is_bounded_and_cancel_stops_reader() -> None:
    frames, audio = buffered(1000, BUFFER_FRAMES * 10)
    try:
        time.sleep(0.01)
        assert frames.remaining == BUFFER_FRAMES * 9
        assert len(audio._frames) == BUFFER_FRAMES
    finally:
        audio.cleanup()
    assert not audio._thread.is_alive()
    assert frames.closed


def test_discard_does_not_read_or_start_prepared_track() -> None:
    _, current = buffered(100, 100)
    next_frames, upcoming = buffered(200, 100)
    mixer = CrossfadeSource(current, volume=1)
    try:
        assert mixer.stage(upcoming, seconds=1, duration=2, on_due=lambda: None)
        assert mixer.discard() is upcoming
        upcoming.cleanup()
        assert next_frames.closed
        assert not mixer.activate(lambda: None)
    finally:
        mixer.cleanup()


def test_prepared_audio_with_failure_is_not_ready() -> None:
    frames = Frames(100, 0)
    frames.current_error = OSError("source failed")
    audio = BufferedAudio(cast(VolumeSource, frames))
    try:
        assert not audio.wait_ready(1)
        with pytest.raises(RuntimeError, match="stream failed"):
            audio.take()
    finally:
        audio.cleanup()


def test_cleanup_retains_prepared_source_until_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, current = buffered(100, 100)
    next_frames, upcoming = buffered(200, 100)
    mixer = CrossfadeSource(current, volume=1)
    mixer.stage(upcoming, seconds=1, duration=2, on_due=lambda: None)
    cleanup = upcoming.cleanup
    attempts = 0

    def fail_once() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Process cleanup failed.")
        cleanup()

    monkeypatch.setattr(upcoming, "cleanup", fail_once)
    try:
        with pytest.raises(RuntimeError, match="Process cleanup failed"):
            mixer.cleanup()
        assert not mixer.closed
        assert not next_frames.closed
        mixer.cleanup()
        assert mixer.closed
        assert next_frames.closed
        assert not upcoming._thread.is_alive()
    finally:
        cleanup()
