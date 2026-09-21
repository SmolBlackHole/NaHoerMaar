# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import math
import struct
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import discord
import pytest

import nahormaar_backend.integrations.discord_voice as discord_voice_module
import nahormaar_backend.integrations.discord_gateway as discord_gateway_module
from nahormaar_backend.application.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioStarted,
    ResolvedTrack,
    TrackError,
    VoiceChannelInfo,
    VoiceDisconnected,
    VoiceError,
    VoiceOutput,
)
from nahormaar_backend.config import ffmpeg_executable
from nahormaar_backend.integrations.audio_sources import (
    AudioFrame,
    _ffmpeg_arguments,
    FFmpegSource,
    MediaStreamError,
    VolumeSource,
)
from nahormaar_backend.integrations.audio_mixer import CrossfadeSource
from nahormaar_backend.integrations.audio_mixer import BufferedAudio
from nahormaar_backend.integrations.discord_voice import DiscordVoice
from nahormaar_backend.integrations.discord_gateway import DiscordGateway
from test_audio_quality import _opus_fixture


class FakeVoiceClient:
    def __init__(self, channel_id: int = 10) -> None:
        self.channel: FakeChannelReference | FakeVoiceChannel = FakeChannelReference(
            channel_id
        )
        self.guild = FakeGuild([])
        self.source: discord.AudioSource | None = None
        self.after: Callable[[Exception | None], None] | None = None
        self.connected = True
        self.stopped = False
        self.paused = False

    def is_connected(self) -> bool:
        return self.connected

    def play(
        self,
        source: discord.AudioSource,
        *,
        after: Callable[[Exception | None], None] | None = None,
        **kwargs: object,
    ) -> None:
        del kwargs
        self.source = source
        self.after = after

    def finish(self, error: Exception | None = None) -> None:
        if self.after is not None:
            self.after(error)

    def stop(self) -> None:
        self.stopped = True

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    async def disconnect(self, *, force: bool = False) -> None:
        del force
        self.connected = False

    async def move_to(self, channel: object) -> None:
        self.channel = cast(FakeChannelReference, channel)


class FailingVoiceClient(FakeVoiceClient):
    def play(
        self,
        source: discord.AudioSource,
        *,
        after: Callable[[Exception | None], None] | None = None,
        **kwargs: object,
    ) -> None:
        del after, kwargs
        self.source = source
        raise discord.ClientException("synthetic play failure")


class FakeChannelReference:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakePermissions:
    def __init__(
        self, *, connect: bool = True, speak: bool = True, view_channel: bool = True
    ) -> None:
        self.connect = connect
        self.speak = speak
        self.view_channel = view_channel


class FakeVoiceChannel:
    def __init__(
        self,
        channel_id: int,
        name: str,
        permissions: FakePermissions | None = None,
    ) -> None:
        self.id = channel_id
        self.name = name
        self._permissions = permissions or FakePermissions()
        self.voice = FakeVoiceClient(channel_id)
        self.guild: FakeGuild

    def permissions_for(self, member: object) -> FakePermissions:
        del member
        return self._permissions

    async def connect(self, **kwargs: object) -> FakeVoiceClient:
        del kwargs
        return self.voice


class FakeGuild:
    def __init__(
        self,
        channels: list[FakeVoiceChannel],
        guild_id: int = 1,
        name: str = "Test server",
    ) -> None:
        self.id = guild_id
        self.name = name
        self.unavailable = False
        self.me = object()
        self.voice_channels = channels
        for channel in channels:
            channel.guild = self
            channel.voice.guild = self
            channel.voice.channel = channel

    def get_channel(self, channel_id: int) -> FakeVoiceChannel | None:
        return next(
            (channel for channel in self.voice_channels if channel.id == channel_id),
            None,
        )


class FakeDiscordClient:
    def __init__(self, *guilds: FakeGuild) -> None:
        self.guilds = list(guilds)
        self.user = SimpleNamespace(id=99)

    def get_channel(self, channel_id: int) -> FakeVoiceChannel | None:
        return next(
            (
                channel
                for guild in self.guilds
                for channel in guild.voice_channels
                if channel.id == channel_id
            ),
            None,
        )


def _adapter(
    tmp_path: Path, volume: float = 1.0, *, gateway: DiscordGateway | None = None
) -> DiscordVoice:
    gateway = gateway or DiscordGateway()
    adapter = DiscordVoice(
        ffmpeg_path=ffmpeg_executable(),
        volume=volume,
        client=gateway.client,
        activity=gateway.set_activity,
    )
    gateway.bind_voice(adapter)
    adapter._voice = cast(discord.VoiceClient, FakeVoiceClient())
    return adapter


def _accepts_voice_output(output: VoiceOutput) -> None:
    del output


def _write_tone(path: Path, amplitude: int = 12_000) -> None:
    sample_rate = 48_000
    frames = bytearray()
    for index in range(sample_rate // 10):
        sample = int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
        frames.extend(struct.pack("<hh", sample, sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def _write_short_tone(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(struct.pack("<hh", 1_000, -1_000) * 100)


class StubFFmpegSource:
    created: ClassVar[list[StubFFmpegSource]] = []

    def __init__(
        self,
        executable: Path,
        source: str,
        headers: tuple[tuple[str, str], ...],
        *,
        opus: bool = False,
        position_seconds: float = 0,
        duration_seconds: float | None = None,
    ) -> None:
        del executable, source, headers, opus
        self.position_seconds = position_seconds
        self.duration_seconds = duration_seconds
        self.process_id = 123
        self.closed = False
        self.current_error: Exception | None = None
        self.created.append(self)

    def cleanup(self) -> None:
        self.closed = True


class StubBufferedAudio:
    def __init__(self, source: StubFFmpegSource) -> None:
        self.source = source

    def cleanup(self) -> None:
        self.source.cleanup()


class StubCrossfadeSource:
    def __init__(
        self,
        current: StubBufferedAudio,
        *,
        volume: float,
        position: float = 0,
        paused: bool = False,
        on_started: Callable[[], None] = lambda: None,
    ) -> None:
        self.current = current
        self.volume = volume
        self._position_seconds = position
        self.on_position_read: Callable[[float], None] | None = None
        self.on_cleanup: Callable[[], None] | None = None
        self.paused = paused
        self.current_error: Exception | None = None
        self.closed = False
        self.transitioning = False
        self.activatable = False
        self.activation_calls = 0
        self.on_started = on_started

    @property
    def position_seconds(self) -> float:
        position = self._position_seconds
        if self.on_position_read is not None:
            self.on_position_read(position)
        return position

    @position_seconds.setter
    def position_seconds(self, position: float) -> None:
        self._position_seconds = position

    def emit_started(self) -> None:
        self.on_started()

    def activate(
        self,
        on_faded: Callable[[], None],
        on_activate: Callable[[], None] = lambda: None,
        on_started: Callable[[], None] = lambda: None,
    ) -> bool:
        del on_faded
        self.activation_calls += 1
        if not self.activatable:
            return False
        self.transitioning = True
        self.position_seconds = 0
        self.on_started = on_started
        on_activate()
        return True

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def cleanup(self) -> None:
        if self.on_cleanup is not None:
            self.on_cleanup()
        self.current.cleanup()
        self.position_seconds = 0
        self.closed = True

    def discard(self) -> None:
        return None


def _stub_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    StubFFmpegSource.created.clear()
    monkeypatch.setattr(discord_voice_module, "FFmpegSource", StubFFmpegSource)
    monkeypatch.setattr(discord_voice_module, "VolumeSource", _stub_volume)
    monkeypatch.setattr(discord_voice_module, "BufferedAudio", StubBufferedAudio)
    monkeypatch.setattr(discord_voice_module, "CrossfadeSource", StubCrossfadeSource)


def _stub_volume(source: StubFFmpegSource, volume: float = 1.0) -> StubFFmpegSource:
    del volume
    return source


def test_typed_start_and_natural_completion_capture_position_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    attempt = uuid4()
    events: list[AudioEvent] = []

    adapter.play(
        ResolvedTrack("unused", duration_seconds=111),
        attempt,
        events.append,
        position_seconds=45,
        paused=True,
    )
    source = cast(StubCrossfadeSource, voice.source)
    assert events == []
    assert StubFFmpegSource.created[0].duration_seconds == 111

    source.position_seconds = 110.5
    source.emit_started()
    source.emit_started()
    voice.finish()

    assert events == [
        AudioStarted(attempt, 110.5),
        AudioCompleted(attempt, AudioEndReason.NATURAL, 110.5),
    ]
    assert source.closed


def test_explicit_stop_is_not_retryable_and_waits_for_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    attempt = uuid4()
    events: list[AudioEvent] = []
    adapter.play(ResolvedTrack("unused"), attempt, events.append)
    source = cast(StubCrossfadeSource, cast(FakeVoiceClient, adapter._voice).source)
    source.position_seconds = 12.5

    asyncio.run(adapter.stop())

    assert events == [
        AudioCompleted(attempt, AudioEndReason.STOPPED, 12.5),
    ]
    assert source.closed
    assert adapter.position_seconds is None


def test_known_early_eof_is_retryable_but_unknown_duration_is_natural(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    early_attempt = uuid4()
    events: list[AudioEvent] = []
    adapter.play(
        ResolvedTrack("known", duration_seconds=111), early_attempt, events.append
    )
    cast(StubCrossfadeSource, voice.source).position_seconds = 45
    voice.finish()

    early = cast(AudioCompleted, events.pop())
    assert early.attempt_id == early_attempt
    assert early.reason is AudioEndReason.INTERRUPTED
    assert isinstance(early.error, TrackError)
    assert early.error.retryable

    unknown_attempt = uuid4()
    adapter.play(ResolvedTrack("unknown"), unknown_attempt, events.append)
    cast(StubCrossfadeSource, voice.source).position_seconds = 45
    voice.finish()
    assert events == [
        AudioCompleted(unknown_attempt, AudioEndReason.NATURAL, 45),
    ]


def test_disconnect_reports_bound_attempt_state_captured_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    attempt = uuid4()
    events: list[tuple[AudioEvent, bool]] = []
    disconnected: list[VoiceDisconnected] = []
    adapter.set_disconnect_handler(disconnected.append)
    adapter.play(
        ResolvedTrack("unused"),
        attempt,
        lambda event: events.append((event, adapter.connected)),
        position_seconds=45,
        paused=True,
    )
    source = cast(StubCrossfadeSource, voice.source)
    source.position_seconds = 45.5

    asyncio.run(adapter._discord_voice_disconnected())

    assert events == [
        (AudioCompleted(attempt, AudioEndReason.INTERRUPTED, 45.5), False),
    ]
    assert disconnected == [VoiceDisconnected(attempt, 10, 45.5, True)]
    assert source.closed


def test_transition_binds_completion_before_start_and_rejects_stale_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    old_attempt, incoming_attempt = uuid4(), uuid4()
    events: list[AudioEvent] = []
    adapter.play(ResolvedTrack("old"), old_attempt, events.append)
    source = cast(StubCrossfadeSource, voice.source)
    stale_started = source.on_started
    source.activatable = True

    assert adapter.start_transition(
        ResolvedTrack("incoming"), incoming_attempt, events.append, lambda: None
    )
    stale_started()
    assert events == []
    source.position_seconds = 0.02
    source.emit_started()
    source.current_error = MediaStreamError("private terminal detail")
    voice.finish(source.current_error)

    assert events[0] == AudioStarted(incoming_attempt, 0.02)
    completed = cast(AudioCompleted, events[1])
    assert completed.attempt_id == incoming_attempt
    assert completed.reason is AudioEndReason.INTERRUPTED
    assert isinstance(completed.error, TrackError)
    assert completed.error.retryable
    assert "private terminal detail" not in str(completed.error)


def test_transition_first_read_failure_uses_incoming_attempt_without_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    incoming_attempt = uuid4()
    events: list[AudioEvent] = []
    adapter.play(ResolvedTrack("old"), uuid4(), events.append)
    source = cast(StubCrossfadeSource, voice.source)
    source.activatable = True

    assert adapter.start_transition(
        ResolvedTrack("incoming"), incoming_attempt, events.append, lambda: None
    )
    source.current_error = MediaStreamError("synthetic first-read failure")
    voice.finish(source.current_error)

    assert len(events) == 1
    completed = cast(AudioCompleted, events[0])
    assert completed.attempt_id == incoming_attempt
    assert completed.reason is AudioEndReason.INTERRUPTED
    assert isinstance(completed.error, TrackError)


def test_completion_retries_snapshot_when_transition_activates_between_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    incoming_attempt = uuid4()
    events: list[AudioEvent] = []
    adapter.play(ResolvedTrack("old"), uuid4(), events.append)
    source = cast(StubCrossfadeSource, voice.source)
    source.position_seconds = 45
    source.activatable = True

    def activate_after_old_position_was_read(position: float) -> None:
        assert position == 45
        source.on_position_read = None
        assert adapter.start_transition(
            ResolvedTrack("incoming"), incoming_attempt, events.append, lambda: None
        )

    source.on_position_read = activate_after_old_position_was_read
    voice.finish()

    assert events == [
        AudioCompleted(incoming_attempt, AudioEndReason.NATURAL, 0),
    ]


def test_transition_is_rejected_after_completion_claims_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    events: list[AudioEvent] = []
    adapter.play(ResolvedTrack("old"), uuid4(), events.append)
    source = cast(StubCrossfadeSource, voice.source)
    source.activatable = True
    transition_results: list[bool] = []

    source.on_cleanup = lambda: transition_results.append(
        adapter.start_transition(
            ResolvedTrack("incoming"), uuid4(), events.append, lambda: None
        )
    )
    voice.finish()

    assert transition_results == [False]
    assert source.activation_calls == 0


def test_cleanup_failure_preserves_pre_cleanup_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audio(monkeypatch)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    attempt = uuid4()
    events: list[AudioEvent] = []
    adapter.play(ResolvedTrack("unused"), attempt, events.append)
    source = cast(StubCrossfadeSource, voice.source)
    source.position_seconds = 37.25

    def fail_after_erasing_position() -> None:
        source.position_seconds = 0
        raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(source, "cleanup", fail_after_erasing_position)
    voice.finish()

    completed = cast(AudioCompleted, events[0])
    assert completed.attempt_id == attempt
    assert completed.position_seconds == 37.25
    assert completed.reason is AudioEndReason.OUTPUT_FAILED
    assert isinstance(completed.error, VoiceError)


def test_packaged_ffmpeg_produces_audio_and_applies_volume(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    full = VolumeSource(
        FFmpegSource(ffmpeg_executable(), str(tone), ()),
        volume=1.0,
    )
    quiet = VolumeSource(
        FFmpegSource(ffmpeg_executable(), str(tone), ()),
        volume=0.25,
    )
    try:
        full_decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
        quiet_decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
        full_frame = full_decoder.decode(full.read(), fec=False)
        quiet_frame = quiet_decoder.decode(quiet.read(), fec=False)
        assert len(full_frame) == len(quiet_frame) == 3_840
        full_peak = max(
            abs(sample[0]) for sample in struct.iter_unpack("<h", full_frame)
        )
        quiet_peak = max(
            abs(sample[0]) for sample in struct.iter_unpack("<h", quiet_frame)
        )
        assert full_peak > 0
        assert quiet_peak == pytest.approx(full_peak * 0.25, rel=0.02)
    finally:
        full.cleanup()
        quiet.cleanup()
    assert full.original.closed
    assert quiet.original.closed


def test_final_short_pcm_chunk_is_padded_to_one_discord_frame(tmp_path: Path) -> None:
    tone = tmp_path / "short-tone.wav"
    _write_short_tone(tone)
    source = FFmpegSource(ffmpeg_executable(), str(tone), ())
    try:
        frame = source.read()
        assert len(frame) == 3_840
        assert frame[:4] == struct.pack("<hh", 1_000, -1_000)
        assert frame[-4:] == b"\0\0\0\0"
        assert source.read() == b""
    finally:
        source.cleanup()


def test_adapter_promotes_callback_and_reaps_both_sources(tmp_path: Path) -> None:
    path = _opus_fixture(tmp_path)

    async def scenario() -> None:
        adapter = _adapter(tmp_path)
        voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
        reported: list[AudioEvent] = []
        old_attempt = uuid4()
        new_attempt = uuid4()
        track = ResolvedTrack(str(path), is_opus=True, duration_seconds=1)
        adapter.play(track, old_attempt, reported.append)
        mixer = cast(CrossfadeSource, voice.source)
        current = mixer.current
        due = threading.Event()
        assert await asyncio.to_thread(current.wait_ready, 50)
        assert await adapter.prepare_next(track, 0.4, due.set)
        assert mixer._prepared is not None
        upcoming = mixer._prepared.audio
        while not due.is_set():
            mixer.read()
        assert adapter.start_transition(
            track, new_attempt, reported.append, lambda: None
        )
        assert adapter.transitioning
        assert mixer.current is upcoming
        mixer._position = 1
        voice.finish()
        assert isinstance(reported[-1], AudioCompleted)
        assert reported[-1].attempt_id == new_attempt
        assert reported[-1].reason is AudioEndReason.NATURAL
        assert current.source.original.closed and upcoming.source.original.closed
        assert not current._thread.is_alive() and not upcoming._thread.is_alive()
        await adapter.stop()

    asyncio.run(scenario())


def test_stop_cancels_a_buffer_that_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _opus_fixture(tmp_path)
    started = threading.Event()
    prepared: list[BufferedAudio] = []

    def blocked_ready(self: BufferedAudio, frames: int) -> bool:
        prepared.append(self)
        started.set()
        with self._condition:
            self._condition.wait_for(lambda: self._stopped, timeout=2)
        return False

    monkeypatch.setattr(BufferedAudio, "wait_ready", blocked_ready)

    async def scenario() -> None:
        adapter = _adapter(tmp_path)
        track = ResolvedTrack(str(path), is_opus=True, duration_seconds=1)
        adapter.play(track, uuid4(), lambda event: None)
        preparation = asyncio.create_task(
            adapter.prepare_next(track, 0.4, lambda: None)
        )
        assert await asyncio.to_thread(started.wait, 1)
        preparation.cancel()
        await adapter.stop()
        with pytest.raises(asyncio.CancelledError):
            await preparation
        assert prepared[0].source.original.closed
        assert not prepared[0]._thread.is_alive()
        assert adapter._preparing is None

    asyncio.run(scenario())


def test_seek_starts_pcm_at_requested_sample(tmp_path: Path) -> None:
    path = tmp_path / "seek.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(struct.pack("<hh", 100, 100) * 48_000)
        output.writeframes(struct.pack("<hh", 200, 200) * 48_000)
    adapter = _adapter(tmp_path, volume=0.4)
    adapter.play(
        ResolvedTrack(str(path)),
        uuid4(),
        lambda event: None,
        position_seconds=1.25,
    )
    try:
        assert adapter._playback is not None
        assert adapter._playback.source.volume == 0.4
        assert adapter._playback.source.current.take(timeout=1) == AudioFrame(
            struct.pack("<hh", 200, 200) * 960
        )
    finally:
        asyncio.run(adapter.stop())


def test_restored_pause_is_silent_until_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        tone = tmp_path / "paused-tone.wav"
        _write_tone(tone)
        adapter = _adapter(tmp_path)
        voice = cast(FakeVoiceClient, adapter._voice)
        try:
            adapter.play(
                ResolvedTrack(str(tone)),
                uuid4(),
                lambda event: None,
                position_seconds=0.02,
                paused=True,
            )
            assert voice.paused
            assert voice.source is not None
            assert adapter._playback is not None
            assert adapter._playback.source.current.wait_ready(1)
            decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
            silent = decoder.decode(voice.source.read(), fec=False)
            assert (
                max(abs(sample[0]) for sample in struct.iter_unpack("<h", silent)) == 0
            )
            assert adapter.position_seconds == 0.02
            adapter.resume()
            assert not voice.paused
            audio = decoder.decode(voice.source.read(), fec=False)
            assert max(abs(sample[0]) for sample in struct.iter_unpack("<h", audio)) > 0
            assert adapter.position_seconds == 0.04
            adapter.pause()
            voice.source.read()
            assert adapter.position_seconds == 0.04
        finally:
            await adapter.stop()
        assert adapter.position_seconds is None

    asyncio.run(scenario())


def test_stop_closes_ffmpeg_process_before_returning(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    adapter.play(ResolvedTrack(str(tone)), uuid4(), lambda event: None)
    source = cast(CrossfadeSource, voice.source)
    process = source.current.source.original._process

    asyncio.run(adapter.stop())

    assert voice.stopped
    assert source.current.source.original.closed
    assert process.poll() is not None


def test_natural_completion_cleans_up_before_callback(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    observed: list[tuple[AudioEvent, bool]] = []
    adapter.play(
        ResolvedTrack(str(tone)),
        uuid4(),
        lambda event: observed.append(
            (event, cast(CrossfadeSource, voice.source).closed)
        ),
    )

    voice.finish()

    assert len(observed) == 1
    assert isinstance(observed[0][0], AudioCompleted)
    assert observed[0][0].reason is AudioEndReason.NATURAL
    assert observed[0][1]


def test_cleanup_failure_is_reported_to_completion_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    reported: list[AudioEvent] = []
    adapter.play(ResolvedTrack(str(tone)), uuid4(), reported.append)
    source = cast(CrossfadeSource, voice.source)
    original_cleanup = source.cleanup

    def fail_cleanup() -> None:
        raise subprocess.TimeoutExpired("ffmpeg", 2)

    monkeypatch.setattr(source, "cleanup", fail_cleanup)
    voice.finish()
    monkeypatch.setattr(source, "cleanup", original_cleanup)
    original_cleanup()

    assert len(reported) == 1
    assert isinstance(reported[0], AudioCompleted)
    assert reported[0].reason is AudioEndReason.OUTPUT_FAILED
    assert isinstance(reported[0].error, VoiceError)
    assert "cleanup failed" in str(reported[0].error)


def test_source_is_closed_only_after_ffmpeg_is_reaped(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    source = FFmpegSource(ffmpeg_executable(), str(tone), ())
    actual_process = source._process
    actual_process.kill()
    actual_process.wait(timeout=2)

    class UnreapedProcess:
        def poll(self) -> None:
            return None

        def kill(self) -> None:
            pass

        def wait(self, timeout: float) -> int:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

    source._process = cast(Any, UnreapedProcess())
    with pytest.raises(subprocess.TimeoutExpired):
        source.cleanup()
    assert not source.closed

    source._process = actual_process
    source.cleanup()
    assert source.closed


def test_stop_runs_ffmpeg_cleanup_outside_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    adapter.play(ResolvedTrack(str(tone)), uuid4(), lambda event: None)
    source = cast(CrossfadeSource, voice.source)
    original_cleanup = source.cleanup
    cleanup_thread: list[int] = []

    def slow_cleanup() -> None:
        cleanup_thread.append(threading.get_ident())
        time.sleep(0.05)
        original_cleanup()

    monkeypatch.setattr(source, "cleanup", slow_cleanup)

    async def stop_with_heartbeat() -> None:
        event_loop_thread = threading.get_ident()
        stop = asyncio.create_task(adapter.stop())
        await asyncio.sleep(0.01)
        assert not stop.done()
        await stop
        assert cleanup_thread
        assert all(thread_id != event_loop_thread for thread_id in cleanup_thread)

    asyncio.run(stop_with_heartbeat())


def test_failed_voice_play_closes_spawned_process(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = FailingVoiceClient()
    adapter._voice = cast(discord.VoiceClient, voice)

    with pytest.raises(VoiceError, match="could not start"):
        adapter.play(ResolvedTrack(str(tone)), uuid4(), lambda event: None)

    source = cast(CrossfadeSource, voice.source)
    assert source.current.source.original.closed
    assert source.current.source.original._process.poll() is not None


def test_media_and_voice_failures_are_classified_without_secrets(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    reported: list[AudioEvent] = []
    signed_value = "signed-secret-value"
    adapter.play(
        ResolvedTrack(
            "https://example.invalid/audio", (("Authorization", signed_value),)
        ),
        uuid4(),
        reported.append,
    )
    voice.finish(RuntimeError(f"upstream exposed {signed_value}"))
    assert isinstance(reported[0], AudioCompleted)
    assert reported[0].reason is AudioEndReason.OUTPUT_FAILED
    assert isinstance(reported[0].error, VoiceError)
    assert signed_value not in str(reported[0].error)

    adapter.play(
        ResolvedTrack("https://example.invalid/audio"), uuid4(), reported.append
    )
    cast(CrossfadeSource, voice.source)._current_error = MediaStreamError(signed_value)
    voice.finish(cast(CrossfadeSource, voice.source)._current_error)
    assert isinstance(reported[1], AudioCompleted)
    assert reported[1].reason is AudioEndReason.INTERRUPTED
    assert isinstance(reported[1].error, TrackError)
    assert reported[1].error.retryable
    assert signed_value not in str(reported[1].error)


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
    with pytest.raises(TrackError, match="invalid HTTP headers"):
        _ffmpeg_arguments(
            Path("ffmpeg.exe"),
            "https://example.invalid/audio",
            (("Authorization", "safe\r\nInjected: value"),),
        )


def test_volume_is_limited_to_documented_range(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.set_volume(0.0)
    adapter.set_volume(1.0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        adapter.set_volume(-0.01)
    with pytest.raises(ValueError, match="between 0 and 1"):
        adapter.set_volume(1.01)


def test_lists_and_joins_regular_voice_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = FakeVoiceChannel(10, "Music")
    denied = FakeVoiceChannel(
        20,
        "Quiet",
        FakePermissions(connect=False, speak=False),
    )
    adapter = _adapter(tmp_path)
    adapter._voice = None
    adapter._client = cast(Any, FakeDiscordClient(FakeGuild([allowed, denied])))
    module_discord = cast(Any, discord_voice_module).discord
    monkeypatch.setattr(module_discord, "VoiceChannel", FakeVoiceChannel)

    assert adapter.channels() == (
        VoiceChannelInfo(10, "Music", True, True, 1, "Test server"),
        VoiceChannelInfo(20, "Quiet", False, False, 1, "Test server"),
    )

    async def connect_and_leave() -> None:
        await adapter.connect(10)
        assert adapter.connected
        assert adapter.channel_id == 10
        await adapter.disconnect()

    asyncio.run(connect_and_leave())
    assert not adapter.connected
    with pytest.raises(VoiceError, match="cannot connect"):
        asyncio.run(adapter.connect(20))


def test_voice_dependencies_are_ready() -> None:
    DiscordVoice.validate_dependencies()


def test_adapter_implements_voice_output_contract(tmp_path: Path) -> None:
    _accepts_voice_output(_adapter(tmp_path))


def test_real_disconnect_reports_once_and_explicit_disconnect_does_not(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    losses: list[str] = []
    adapter.set_disconnect_handler(lambda event: losses.append("lost"))
    asyncio.run(adapter._discord_voice_disconnected())
    asyncio.run(adapter._discord_voice_disconnected())
    assert losses == ["lost"]

    replacement = FakeVoiceClient(channel_id=20)
    adapter._voice = cast(discord.VoiceClient, replacement)
    asyncio.run(adapter.disconnect())
    assert losses == ["lost"]


def test_disconnect_from_old_connection_cannot_clear_replacement(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    old = cast(discord.VoiceClient, cast(Any, adapter._voice))
    replacement = cast(discord.VoiceClient, FakeVoiceClient(channel_id=20))
    adapter._voice = replacement

    asyncio.run(adapter._disconnect_voice(old, report_loss=True))

    assert adapter._voice is replacement


def test_intentional_disconnect_tag_protects_replacement(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    replacement = cast(discord.VoiceClient, FakeVoiceClient(channel_id=10))
    adapter._voice = replacement
    adapter._expected_disconnects[10] = 1

    assert adapter._consume_expected_disconnect(10)
    assert adapter._voice is replacement
    assert not adapter._consume_expected_disconnect(10)


def test_disconnect_stops_paused_audio_before_leaving(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    adapter.play(ResolvedTrack(str(tone)), uuid4(), lambda event: None)
    adapter.pause()

    asyncio.run(adapter.disconnect())

    assert voice.stopped
    assert not voice.connected
    source = cast(CrossfadeSource, voice.source)
    assert source.current.source.original.closed
    assert source.current.source.original._process.poll() is not None


def test_ready_without_any_guild_allows_inviting_the_bot_later(tmp_path: Path) -> None:
    gateway = DiscordGateway()
    adapter = _adapter(tmp_path, gateway=gateway)
    gateway._discord_ready()
    asyncio.run(asyncio.wait_for(gateway.wait_until_ready(), timeout=1))
    assert adapter.channels() == ()


def test_channel_discovery_tracks_joined_and_removed_guilds(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    client = FakeDiscordClient()
    adapter._client = cast(Any, client)
    first = FakeGuild([FakeVoiceChannel(10, "General")], 1, "First server")
    second = FakeGuild([FakeVoiceChannel(20, "General")], 2, "Second server")
    assert adapter.channels() == ()
    client.guilds.extend([second, first])
    assert [
        (channel.id, channel.guild_id, channel.guild_name)
        for channel in adapter.channels()
    ] == [(10, 1, "First server"), (20, 2, "Second server")]
    first.unavailable = True
    assert [channel.id for channel in adapter.channels()] == [20]
    client.guilds.remove(second)
    assert adapter.channels() == ()


def test_switching_guilds_closes_old_voice_and_ignores_late_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = FakeVoiceChannel(10, "General")
    second = FakeVoiceChannel(20, "General")
    first_guild = FakeGuild([first], 1)
    second_guild = FakeGuild([second], 2)
    gateway = DiscordGateway()
    adapter = _adapter(tmp_path, gateway=gateway)
    adapter._voice = None
    adapter._client = cast(Any, FakeDiscordClient(first_guild, second_guild))
    monkeypatch.setattr(
        cast(Any, discord_voice_module).discord, "VoiceChannel", FakeVoiceChannel
    )
    losses: list[str] = []
    adapter.set_disconnect_handler(lambda event: losses.append("lost"))
    original_connect = second.connect

    async def connect_second(**kwargs: object) -> FakeVoiceClient:
        assert not first.voice.connected
        return await original_connect(**kwargs)

    monkeypatch.setattr(second, "connect", connect_second)

    async def scenario() -> None:
        await adapter.connect(10)
        await adapter.connect(20)
        assert adapter.channel_id == 20
        member = cast(discord.Member, SimpleNamespace(id=99, guild=first_guild))
        before = cast(discord.VoiceState, SimpleNamespace(channel=first))
        after = cast(discord.VoiceState, SimpleNamespace(channel=None))
        await gateway.client.on_voice_state_update(member, before, after)
        await gateway.client.on_voice_state_update(member, before, after)
        await gateway.client.on_guild_remove(cast(discord.Guild, first_guild))
        assert adapter.channel_id == 20
        assert losses == []
        await gateway.client.on_guild_remove(cast(discord.Guild, second_guild))
        assert not adapter.connected
        assert not second.voice.connected
        assert losses == ["lost"]

    asyncio.run(scenario())


def test_inaccessible_or_stale_channels_cannot_be_joined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = FakeVoiceChannel(10, "Hidden", FakePermissions(view_channel=False))
    muted = FakeVoiceChannel(20, "Muted", FakePermissions(speak=False))
    guild = FakeGuild([hidden, muted])
    adapter = _adapter(tmp_path)
    adapter._voice = None
    adapter._client = cast(Any, FakeDiscordClient(guild))
    monkeypatch.setattr(
        cast(Any, discord_voice_module).discord, "VoiceChannel", FakeVoiceChannel
    )
    assert not adapter.channels()[0].can_connect
    with pytest.raises(VoiceError, match="cannot connect"):
        asyncio.run(adapter.connect(10))
    with pytest.raises(VoiceError, match="cannot speak"):
        asyncio.run(adapter.connect(20))
    with pytest.raises(VoiceError, match="unavailable"):
        asyncio.run(adapter.connect(999))
    guild.unavailable = True
    with pytest.raises(VoiceError, match="unavailable"):
        asyncio.run(adapter.connect(10))


def test_activity_tracks_audio_controls_without_changing_the_audio_source(
    tmp_path: Path,
) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    gateway = DiscordGateway()
    adapter = _adapter(tmp_path, gateway=gateway)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    adapter.play(
        ResolvedTrack(str(tone), title="Song\nname", uploader="Artist"),
        uuid4(),
        lambda event: None,
    )
    source = voice.source
    try:
        playing = gateway._activity.to_dict()
        assert playing["type"] == discord.ActivityType.listening.value
        assert playing["name"] == "Song name"
        assert playing.get("state") == "Artist"
        adapter.pause()
        assert gateway._activity.to_dict().get("state") == "Pausiert: Song name"
        adapter.resume()
        assert gateway._activity.to_dict() == playing
        adapter.set_volume(0.3)
        assert gateway._activity.to_dict() == playing
        assert voice.source is source
    finally:
        asyncio.run(adapter.disconnect())
    assert gateway._activity.to_dict().get("state") == "Bereit für Musik"


def test_activity_limits_text_and_never_uses_stream_urls_as_fallback(
    tmp_path: Path,
) -> None:
    gateway = DiscordGateway()
    gateway.set_activity(ResolvedTrack("https://example.invalid/?secret=hidden"))
    assert gateway._activity.to_dict()["name"] == "YouTube"
    assert "hidden" not in str(gateway._activity.to_dict())
    track = ResolvedTrack("unused", title="x" * 500, uploader="y" * 500)
    gateway.set_activity(track)
    assert len(gateway._activity.to_dict()["name"]) == 128
    assert len(gateway._activity.to_dict().get("state") or "") == 128
    gateway.set_activity(track, paused=True)
    assert len(gateway._activity.to_dict().get("state") or "") == 128


class FakePresenceClient(FakeDiscordClient):
    def __init__(self, gateway: DiscordGateway, *, fail_first: bool = False) -> None:
        super().__init__(FakeGuild([]))
        self.gateway = gateway
        self.closed = asyncio.Event()
        self.ready = True
        self.fail_first = fail_first
        self.attempts: list[tuple[float, discord.BaseActivity]] = []

    async def start(self, token: str) -> None:
        del token
        self.gateway._discord_ready()
        await self.closed.wait()

    def is_ready(self) -> bool:
        return self.ready

    async def change_presence(self, *, activity: discord.BaseActivity) -> None:
        self.attempts.append((asyncio.get_running_loop().time(), activity))
        if self.fail_first and len(self.attempts) == 1:
            raise ConnectionError("synthetic error containing secret data")

    async def application_info(self) -> discord.AppInfo:
        # Keep profile work pending until shutdown, without making a REST request.
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed.set()

    async def wait_for_attempts(self, count: int) -> None:
        async with asyncio.timeout(2):
            while len(self.attempts) < count:
                await asyncio.sleep(0.001)


def test_presence_coalesces_changes_resends_after_reconnect_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interval = 0.02
    monkeypatch.setattr(discord_gateway_module, "_PRESENCE_INTERVAL_SECONDS", interval)

    async def scenario() -> None:
        real_sleep = asyncio.sleep
        sleep_requests: asyncio.Queue[float] = asyncio.Queue()
        ticks: asyncio.Queue[None] = asyncio.Queue()

        async def controlled_sleep(delay: float) -> None:
            if delay == interval:
                sleep_requests.put_nowait(delay)
                await ticks.get()
            else:
                await real_sleep(delay)

        async def wait_for_interval() -> None:
            async with asyncio.timeout(2):
                assert await sleep_requests.get() == interval

        async def next_interval() -> None:
            ticks.put_nowait(None)
            await wait_for_interval()

        monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
        gateway = DiscordGateway()
        client = FakePresenceClient(gateway)
        gateway.client = cast(discord_gateway_module._DiscordClient, client)
        task = asyncio.create_task(gateway.start("test-token", prepare=lambda: None))
        try:
            await wait_for_interval()
            assert len(client.attempts) == 1
            assert client.attempts[0][1].to_dict().get("state") == "Bereit für Musik"
            for title in ("First", "Skipped", "Current"):
                gateway.set_activity(ResolvedTrack("unused", title=title))
            assert len(client.attempts) == 1
            await next_interval()
            assert len(client.attempts) == 2
            assert client.attempts[1][1].to_dict()["name"] == "Current"
            await next_interval()
            assert len(client.attempts) == 2
            client.ready = False
            gateway.set_activity(ResolvedTrack("unused", title="After reconnect"))
            await next_interval()
            assert len(client.attempts) == 2
            client.ready = True
            gateway._discord_ready()
            assert len(client.attempts) == 2
            await next_interval()
            assert len(client.attempts) == 3
            gateway._discord_ready()
            await next_interval()
            assert len(client.attempts) == 4
            assert client.attempts[2][1].to_dict() == client.attempts[3][1].to_dict()
        finally:
            presence = gateway._presence_task
            bio = gateway._bio_task
            await gateway.close()
            await task
        assert presence is not None and presence.done()
        assert bio is not None and bio.done()
        assert client.closed.is_set()

    asyncio.run(scenario())


def test_presence_failure_retries_without_stopping_the_bot_or_logging_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(discord_gateway_module, "_PRESENCE_INTERVAL_SECONDS", 0.02)

    async def scenario() -> None:
        gateway = DiscordGateway()
        client = FakePresenceClient(gateway, fail_first=True)
        gateway.client = cast(discord_gateway_module._DiscordClient, client)
        task = asyncio.create_task(gateway.start("test-token", prepare=lambda: None))
        try:
            await client.wait_for_attempts(2)
            assert not task.done()
            assert gateway._sent_activity is not None
            assert client.attempts[0][1].to_dict() == client.attempts[1][1].to_dict()
        finally:
            await gateway.close()
            await task

    asyncio.run(scenario())
    assert "presence update failed (ConnectionError)" in caplog.text
    assert "secret data" not in caplog.text


def test_gateway_intents_and_voice_ownership_are_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        gateway = DiscordGateway()
        adapter = _adapter(tmp_path, gateway=gateway)
        close_client = AsyncMock()
        monkeypatch.setattr(gateway.client, "close", close_client)
        expected = discord.Intents.none()
        expected.guilds = True
        expected.voice_states = True
        assert gateway.client.intents == expected

        await adapter.close()
        await adapter.close()
        assert not adapter.connected
        close_client.assert_not_awaited()

        await gateway.close()
        await gateway.close()
        close_client.assert_awaited_once()

    asyncio.run(scenario())


def test_gateway_close_does_not_close_session_owned_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        gateway = DiscordGateway()
        voice = Mock(spec=DiscordVoice)
        gateway.bind_voice(voice)
        close_client = AsyncMock()
        monkeypatch.setattr(gateway.client, "close", close_client)

        await gateway.close()

        close_client.assert_awaited_once()
        voice.close.assert_not_called()
        voice.disconnect.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["dependency", "login", "client"])
def test_gateway_startup_failure_wakes_waiters_and_reaps_owned_tasks(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        gateway = DiscordGateway()
        prepare = Mock()
        start_client = AsyncMock()
        close_client = AsyncMock()
        monkeypatch.setattr(gateway.client, "start", start_client)
        monkeypatch.setattr(gateway.client, "close", close_client)
        if failure == "dependency":
            prepare.side_effect = VoiceError("Synthetic dependency unavailable.")
            expected = "dependency unavailable"
        elif failure == "login":
            start_client.side_effect = discord.LoginFailure("private detail")
            expected = "authentication failed"
        else:
            start_client.side_effect = RuntimeError("private detail")
            expected = "client failed to start"

        with pytest.raises(VoiceError, match=expected):
            await gateway.start("test-token", prepare=prepare)
        with pytest.raises(VoiceError, match=expected):
            await asyncio.wait_for(gateway.wait_until_ready(), timeout=1)
        await gateway.close()
        close_client.assert_awaited_once()
        prepare.assert_called_once_with()
        assert gateway._presence_task is None
        assert gateway._bio_task is None
        if failure == "dependency":
            start_client.assert_not_awaited()
        else:
            start_client.assert_awaited_once_with("test-token")

    asyncio.run(scenario())


def test_cancelling_gateway_start_reaps_profile_tasks_and_client() -> None:
    async def scenario() -> None:
        gateway = DiscordGateway()
        client = FakePresenceClient(gateway)
        gateway.client = cast(discord_gateway_module._DiscordClient, client)
        task = asyncio.create_task(gateway.start("test-token", prepare=lambda: None))
        await gateway.wait_until_ready()
        presence, bio = gateway._presence_task, gateway._bio_task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert presence is not None and presence.done()
        assert bio is not None and bio.done()
        assert client.closed.is_set()

    asyncio.run(scenario())
