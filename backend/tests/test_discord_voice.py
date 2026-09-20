# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Callable
import math
from pathlib import Path
import struct
import subprocess
import threading
import time
from typing import Any, cast
import wave

import discord
import pytest

import nahormaar_backend.discord_voice as discord_voice_module
from nahormaar_backend.audio import (
    ResolvedTrack,
    TrackError,
    VoiceChannelInfo,
    VoiceError,
    VoiceOutput,
)
from nahormaar_backend.config import ffmpeg_executable
from nahormaar_backend.discord_voice import (
    DiscordVoice,
    _FFmpegSource,
    _MediaStreamError,
    _VolumeSource,
    _ffmpeg_arguments,
)


class FakeVoiceClient:
    def __init__(self, channel_id: int = 10) -> None:
        self.channel = FakeChannelReference(channel_id)
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
    def __init__(self, *, connect: bool = True, speak: bool = True) -> None:
        self.connect = connect
        self.speak = speak


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

    def permissions_for(self, member: object) -> FakePermissions:
        del member
        return self._permissions

    async def connect(self, **kwargs: object) -> FakeVoiceClient:
        del kwargs
        return self.voice


class FakeGuild:
    id = 1
    name = "Test server"

    def __init__(self, channels: list[FakeVoiceChannel]) -> None:
        self.me = object()
        self.voice_channels = channels

    def get_channel(self, channel_id: int) -> FakeVoiceChannel | None:
        return next(
            (channel for channel in self.voice_channels if channel.id == channel_id),
            None,
        )


class FakeDiscordClient:
    def __init__(self, guild: FakeGuild | None) -> None:
        self.guild = guild

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        del guild_id
        return self.guild


def _adapter(tmp_path: Path, volume: float = 1.0) -> DiscordVoice:
    adapter = DiscordVoice(
        guild_id=1,
        ffmpeg_path=ffmpeg_executable(),
        volume=volume,
    )
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


def test_packaged_ffmpeg_produces_audio_and_applies_volume(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    full = _VolumeSource(
        _FFmpegSource(ffmpeg_executable(), str(tone), ()),
        volume=1.0,
    )
    quiet = _VolumeSource(
        _FFmpegSource(ffmpeg_executable(), str(tone), ()),
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
    source = _FFmpegSource(ffmpeg_executable(), str(tone), ())
    try:
        frame = source.read()
        assert len(frame) == 3_840
        assert frame[:4] == struct.pack("<hh", 1_000, -1_000)
        assert frame[-4:] == b"\0\0\0\0"
        assert source.read() == b""
    finally:
        source.cleanup()


def test_seek_starts_pcm_at_requested_sample(tmp_path: Path) -> None:
    path = tmp_path / "seek.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(struct.pack("<hh", 100, 100) * 48_000)
        output.writeframes(struct.pack("<hh", 200, 200) * 48_000)
    adapter = _adapter(tmp_path, volume=0.4)
    adapter.play(ResolvedTrack(str(path)), lambda error: None, position_seconds=1.25)
    try:
        assert adapter._playback is not None
        assert adapter._playback.source.volume == 0.4
        assert (
            adapter._playback.source.original.read()
            == struct.pack("<hh", 200, 200) * 960
        )
    finally:
        asyncio.run(adapter.stop())


def test_stop_closes_ffmpeg_process_before_returning(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    adapter.play(ResolvedTrack(str(tone)), lambda error: None)
    source = cast(_VolumeSource, voice.source)
    process = source.original._process

    asyncio.run(adapter.stop())

    assert voice.stopped
    assert source.original.closed
    assert process.poll() is not None


def test_natural_completion_cleans_up_before_callback(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    observed: list[tuple[Exception | None, bool]] = []
    adapter.play(
        ResolvedTrack(str(tone)),
        lambda error: observed.append(
            (error, cast(_VolumeSource, voice.source).original.closed)
        ),
    )

    voice.finish()

    assert observed == [(None, True)]


def test_cleanup_failure_is_reported_to_completion_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    reported: list[Exception | None] = []
    adapter.play(ResolvedTrack(str(tone)), reported.append)
    source = cast(_VolumeSource, voice.source)
    original_cleanup = source.cleanup

    def fail_cleanup() -> None:
        raise subprocess.TimeoutExpired("ffmpeg", 2)

    monkeypatch.setattr(source, "cleanup", fail_cleanup)
    voice.finish()
    monkeypatch.setattr(source, "cleanup", original_cleanup)
    original_cleanup()

    assert len(reported) == 1
    assert isinstance(reported[0], VoiceError)
    assert "cleanup failed" in str(reported[0])


def test_source_is_closed_only_after_ffmpeg_is_reaped(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    source = _FFmpegSource(ffmpeg_executable(), str(tone), ())
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
    adapter.play(ResolvedTrack(str(tone)), lambda error: None)
    source = cast(_VolumeSource, voice.source)
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
        adapter.play(ResolvedTrack(str(tone)), lambda error: None)

    source = cast(_VolumeSource, voice.source)
    assert source.original.closed
    assert source.original._process.poll() is not None


def test_media_and_voice_failures_are_classified_without_secrets(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    reported: list[Exception | None] = []
    signed_value = "signed-secret-value"
    adapter.play(
        ResolvedTrack(
            "https://example.invalid/audio", (("Authorization", signed_value),)
        ),
        reported.append,
    )
    voice.finish(RuntimeError(f"upstream exposed {signed_value}"))
    assert isinstance(reported[0], VoiceError)
    assert signed_value not in str(reported[0])

    adapter.play(ResolvedTrack("https://example.invalid/audio"), reported.append)
    cast(_VolumeSource, voice.source).original._current_error = _MediaStreamError(
        signed_value
    )
    voice.finish(cast(_VolumeSource, voice.source).original.current_error)
    assert isinstance(reported[1], TrackError)
    assert reported[1].retryable
    assert signed_value not in str(reported[1])


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
    DiscordVoice._validate_voice_dependencies()


def test_adapter_implements_voice_output_contract(tmp_path: Path) -> None:
    _accepts_voice_output(_adapter(tmp_path))


def test_real_disconnect_reports_once_and_explicit_disconnect_does_not(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    losses: list[str] = []
    adapter.set_disconnect_handler(lambda: losses.append("lost"))
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
    adapter.play(ResolvedTrack(str(tone)), lambda error: None)
    adapter.pause()

    asyncio.run(adapter.disconnect())

    assert voice.stopped
    assert not voice.connected
    source = cast(_VolumeSource, voice.source)
    assert source.original.closed
    assert source.original._process.poll() is not None


def test_wait_until_ready_reports_missing_configured_guild(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter._ready.set()
    with pytest.raises(VoiceError, match="configured Discord guild"):
        asyncio.run(asyncio.wait_for(adapter.wait_until_ready(), timeout=1))


def test_activity_tracks_audio_controls_without_changing_the_audio_source(
    tmp_path: Path,
) -> None:
    tone = tmp_path / "tone.wav"
    _write_tone(tone)
    adapter = _adapter(tmp_path)
    voice = cast(FakeVoiceClient, cast(Any, adapter._voice))
    adapter.play(
        ResolvedTrack(str(tone), title="Song\nname", uploader="Artist"),
        lambda error: None,
    )
    source = voice.source
    try:
        playing = adapter._activity.to_dict()
        assert playing["type"] == discord.ActivityType.listening.value
        assert playing["name"] == "Song name"
        assert playing.get("state") == "Artist"
        adapter.pause()
        assert adapter._activity.to_dict().get("state") == "Pausiert: Song name"
        adapter.resume()
        assert adapter._activity.to_dict() == playing
        adapter.set_volume(0.3)
        assert adapter._activity.to_dict() == playing
        assert voice.source is source
    finally:
        asyncio.run(adapter.disconnect())
    assert adapter._activity.to_dict().get("state") == "Bereit für Musik"


def test_activity_limits_text_and_never_uses_stream_urls_as_fallback(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    adapter._set_activity(ResolvedTrack("https://example.invalid/?secret=hidden"))
    assert adapter._activity.to_dict()["name"] == "YouTube"
    assert "hidden" not in str(adapter._activity.to_dict())
    track = ResolvedTrack("unused", title="x" * 500, uploader="y" * 500)
    adapter._set_activity(track)
    assert len(adapter._activity.to_dict()["name"]) == 128
    assert len(adapter._activity.to_dict().get("state") or "") == 128
    adapter._set_activity(track, paused=True)
    assert len(adapter._activity.to_dict().get("state") or "") == 128


class FakePresenceClient(FakeDiscordClient):
    def __init__(self, adapter: DiscordVoice, *, fail_first: bool = False) -> None:
        super().__init__(FakeGuild([]))
        self.adapter = adapter
        self.closed = asyncio.Event()
        self.ready = True
        self.fail_first = fail_first
        self.attempts: list[tuple[float, discord.BaseActivity]] = []

    async def start(self, token: str) -> None:
        del token
        self.adapter._discord_ready()
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
    monkeypatch.setattr(discord_voice_module, "_PRESENCE_INTERVAL_SECONDS", 0.02)

    async def scenario() -> None:
        adapter = _adapter(tmp_path)
        client = FakePresenceClient(adapter)
        adapter._client = cast(discord_voice_module._DiscordClient, client)
        task = asyncio.create_task(adapter.start("test-token"))
        try:
            await client.wait_for_attempts(1)
            assert client.attempts[0][1].to_dict().get("state") == "Bereit für Musik"
            for title in ("First", "Skipped", "Current"):
                adapter._set_activity(ResolvedTrack("unused", title=title))
            await client.wait_for_attempts(2)
            assert client.attempts[1][1].to_dict()["name"] == "Current"
            await asyncio.sleep(0.05)
            assert len(client.attempts) == 2
            client.ready = False
            adapter._set_activity(ResolvedTrack("unused", title="After reconnect"))
            await asyncio.sleep(0.05)
            assert len(client.attempts) == 2
            client.ready = True
            adapter._discord_ready()
            await client.wait_for_attempts(3)
            adapter._discord_ready()
            await client.wait_for_attempts(4)
            assert client.attempts[2][1].to_dict() == client.attempts[3][1].to_dict()
            assert all(
                later[0] - earlier[0] >= 0.02
                for earlier, later in zip(
                    client.attempts, client.attempts[1:], strict=False
                )
            )
        finally:
            presence = adapter._presence_task
            bio = adapter._bio_task
            await adapter.close()
            await task
        assert presence is not None and presence.done()
        assert bio is not None and bio.done()
        assert client.closed.is_set()

    asyncio.run(scenario())


def test_presence_failure_retries_without_stopping_the_bot_or_logging_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(discord_voice_module, "_PRESENCE_INTERVAL_SECONDS", 0.02)

    async def scenario() -> None:
        adapter = _adapter(tmp_path)
        client = FakePresenceClient(adapter, fail_first=True)
        adapter._client = cast(discord_voice_module._DiscordClient, client)
        task = asyncio.create_task(adapter.start("test-token"))
        try:
            await client.wait_for_attempts(2)
            assert not task.done()
            assert adapter._sent_activity is not None
            assert client.attempts[0][1].to_dict() == client.attempts[1][1].to_dict()
        finally:
            await adapter.close()
            await task

    asyncio.run(scenario())
    assert "presence update failed (ConnectionError)" in caplog.text
    assert "secret data" not in caplog.text
