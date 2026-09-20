# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Run the real audio pipeline with a recording transport, never Discord sockets."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import audioop
import json
import math
import struct
import subprocess
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import discord
import pytest
from discord.opus import OPUS_SILENCE
from discord.player import AudioPlayer

from nahormaar_backend.application.audio import ResolvedTrack
from nahormaar_backend.application.playback import PlaybackController
from nahormaar_backend.config import ffmpeg_executable
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.models import PlaybackState, QueueEntry
from nahormaar_backend.integrations.audio_mixer import CrossfadeSource
from nahormaar_backend.integrations.audio_sources import AudioFrame, FrameEncoder
from nahormaar_backend.integrations.discord_voice import DiscordVoice, _DiscordClient
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_discord_voice import (
    FakeDiscordClient,
    FakeGuild,
    FakeVoiceChannel,
    FakeVoiceClient,
)


@dataclass(frozen=True)
class RecordedFrame:
    packet: bytes
    pcm: bytes | None
    position: float
    fading: bool
    sent_at: float
    volume: float
    paused: bool


class RecordingEncoder(FrameEncoder):
    """Observe the mixer output before Opus applies its post-pause decoder ramp."""

    def __init__(self) -> None:
        super().__init__()
        self.pcm = b""

    def encode(self, frame: AudioFrame, volume: float) -> bytes:
        self.pcm = audioop.mul(frame.pcm, 2, volume)
        return super().encode(frame, volume)


class RecordingVoice(FakeVoiceClient):
    """Keep discord.py's timing and callbacks; replace only its network transport."""

    def __init__(self) -> None:
        super().__init__(7)
        self.frames: list[RecordedFrame] = []
        self.players: list[AudioPlayer] = []
        self.client = SimpleNamespace(loop=asyncio.get_running_loop())
        self.ws = self
        self.encoder = RecordingEncoder()

    async def speak(self, speaking: object) -> None:
        pass

    def send_audio_packet(self, packet: bytes, *, encode: bool) -> None:
        assert not encode
        source = cast(CrossfadeSource, self.source)
        self.frames.append(
            RecordedFrame(
                packet,
                self.encoder.pcm if packet != OPUS_SILENCE else None,
                source.position_seconds,
                source.transitioning,
                time.monotonic(),
                source.volume,
                self.paused,
            )
        )

    def play(
        self,
        source: discord.AudioSource,
        *,
        after: Callable[[Exception | None], None] | None = None,
        **kwargs: object,
    ) -> None:
        super().play(source, after=after, **kwargs)
        cast(CrossfadeSource, source)._encoder = self.encoder
        player = AudioPlayer(source, cast(discord.VoiceClient, self), after=after)
        self.players.append(player)
        player.start()

    def stop(self) -> None:
        super().stop()
        if self.players:
            self.players[-1].stop()

    def pause(self) -> None:
        super().pause()
        if self.players:
            self.players[-1].pause()

    def resume(self) -> None:
        super().resume()
        if self.players:
            self.players[-1].resume()

    async def join_threads(self) -> None:
        for player in self.players:
            await asyncio.to_thread(player.join, 3)
            assert not player.is_alive()
            assert player._current_error is None


class LocalResolver:
    def __init__(self, tracks: dict[str, ResolvedTrack]) -> None:
        self.tracks = tracks
        self.calls: list[str] = []

    async def resolve(self, source_url: str) -> ResolvedTrack:
        self.calls.append(source_url)
        return self.tracks[source_url]


def make_tone(
    folder: Path, frequency: int, *, opus: bool, seconds: int = 8
) -> ResolvedTrack:
    path = folder / f"tone-{frequency}.wav"
    second = b"".join(
        struct.pack("<hh", value, value)
        for index in range(48000)
        for value in (int(10000 * math.sin(2 * math.pi * frequency * index / 48000)),)
    )
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(second * seconds)
    if opus:
        encoded = path.with_suffix(".webm")
        subprocess.run(  # noqa: S603 - fixed local FFmpeg and generated fixture paths
            [
                str(ffmpeg_executable()),
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-c:a",
                "libopus",
                "-b:a",
                "128k",
                "-frame_duration",
                "20",
                str(encoded),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        path = encoded
    return ResolvedTrack(
        str(path), duration_seconds=seconds, title=f"Tone {frequency}", is_opus=opus
    )


async def open_player(
    path: Path,
    resolver: LocalResolver,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PlaybackController, DiscordVoice, RecordingVoice]:
    monkeypatch.setattr(discord, "VoiceChannel", FakeVoiceChannel)
    recording = RecordingVoice()
    channel = FakeVoiceChannel(7, "Offline recorder")
    channel.voice = recording
    adapter = DiscordVoice(ffmpeg_executable())
    adapter._client = cast(_DiscordClient, FakeDiscordClient(FakeGuild([channel])))
    controller = await PlaybackController.create(path, resolver, adapter)
    return controller, adapter, recording


async def wait_for(predicate: Callable[[], bool], seconds: float = 20) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


def decode_recording(recording: RecordingVoice, path: Path) -> list[bytes]:
    decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
    frames = [decoder.decode(frame.packet, fec=False) for frame in recording.frames]
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(frames))
    return frames


def amplitude(pcm: bytes, frequency: int) -> float:
    """Measure one known tone, independently of its phase and the other tone."""
    samples = [left for left, _ in struct.iter_unpack("<hh", pcm)]
    coefficient = 2 * math.cos(2 * math.pi * frequency / 48000)
    previous = before = 0.0
    for sample in samples:
        current = sample + coefficient * previous - before
        before, previous = previous, current
    power = previous * previous + before * before - coefficient * previous * before
    return 2 * math.sqrt(max(0, power)) / len(samples)


@pytest.mark.parametrize(
    ("opus", "fade", "controls"),
    [(False, 3, False), (True, 3, False), (True, 5, False), (True, 5, True)],
    ids=["pcm-3s", "opus-3s", "opus-5s", "opus-5s-controls"],
)
def test_real_audio_thread_crossfades_without_cut_or_second_player(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    opus: bool,
    fade: int,
    controls: bool,
) -> None:
    async def scenario() -> None:
        tracks = {
            f"fixture:{i}": make_tone(
                tmp_path, frequency, opus=opus, seconds=2 * fade + 2
            )
            for i, frequency in enumerate((400, 1000))
        }
        resolver = LocalResolver(tracks)
        controller, adapter, recording = await open_player(
            tmp_path / "offline.sqlite3", resolver, monkeypatch
        )
        try:
            for url in tracks:
                await controller.enqueue(QueueEntry(url))
            await controller.request(uuid4(), commands.Crossfade(fade))
            await controller.connect(7)
            await controller.play()
            await wait_for(lambda: len(controller.snapshot.recently_played) == 2)
            await controller.read_status()
            if controls:
                await wait_for(lambda: (adapter.position_seconds or 0) >= 1)
                await controller.pause()
                position = adapter.position_seconds
                await controller.set_volume(0.4)
                await asyncio.sleep(0.15)
                assert adapter.position_seconds == position
                assert adapter.transitioning
                await controller.play()
            await wait_for(lambda: controller.snapshot.state is PlaybackState.IDLE)
            assert controller.status.last_issue is None
            assert len(recording.players) == 1  # No stop/start at the overlap.
            assert resolver.calls == list(tracks)
            assert adapter.position_seconds is None
        finally:
            await controller.close()
            await recording.join_threads()

        frames = decode_recording(recording, tmp_path / "crossfade.wav")
        # Natural fades are measured after an Opus decode. For explicit pause,
        # measure the mixer PCM, including the first resumed frame: Discord sends
        # DTX silence during pause and the decoder then applies its own gain ramp.
        # That codec behavior must not masquerade as a discontinuity in the mixer.
        mixed = [
            (info.pcm if controls else pcm, info)
            for pcm, info in zip(frames, recording.frames, strict=True)
            if info.fading
            and not info.paused
            and info.pcm is not None
            and 0.2 < info.position < fade - 0.2
        ]
        assert len(mixed) >= (fade - 0.5) * 50
        minimum_rms = min(audioop.rms(pcm, 2) / info.volume for pcm, info in mixed)
        assert minimum_rms > 3500, [
            (info.position, info.volume, audioop.rms(pcm, 2), info.packet.hex()[:16])
            for pcm, info in mixed
            if audioop.rms(pcm, 2) / info.volume < 3500
        ]
        levels = [
            (amplitude(pcm, 400) / info.volume, amplitude(pcm, 1000) / info.volume)
            for pcm, info in mixed
        ]
        assert levels[0][0] > 9000 and levels[0][1] < 1000
        assert levels[-1][0] < 1000 and levels[-1][1] > 9000
        assert sum(left > 2500 and right > 2500 for left, right in levels) >= 45
        assert all(8500 < left + right < 11500 for left, right in levels), [
            (info.position, left, right)
            for (_, info), (left, right) in zip(mixed, levels, strict=True)
            if not 8500 < left + right < 11500
        ]
        assert (
            max(abs(levels[i + 1][1] - levels[i][1]) for i in range(len(levels) - 1))
            < 1500
        )
        (tmp_path / "measurement.json").write_text(
            json.dumps(
                {
                    "fade_seconds": fade,
                    "input": "Opus/WebM" if opus else "PCM/WAV",
                    "audio_threads": len(recording.players),
                    "measured_fade_frames": len(mixed),
                    "normalized_minimum_rms": round(minimum_rms),
                    "first_tone_levels": [round(level) for level in levels[0]],
                    "last_tone_levels": [round(level) for level in levels[-1]],
                    "pause_and_volume_during_fade": controls,
                    "measurement_stage": "mixer PCM" if controls else "decoded Opus",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("paused", [False, True])
def test_real_audio_restart_resumes_saved_position_and_keeps_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    paused: bool,
) -> None:
    async def scenario() -> None:
        track = make_tone(tmp_path, 400, opus=False)
        resolver = LocalResolver({"fixture:first": track})
        database = tmp_path / "offline.sqlite3"
        controller, adapter, recording = await open_player(
            database, resolver, monkeypatch
        )
        try:
            await controller.enqueue(QueueEntry("fixture:first"))
            await controller.connect(7)
            await controller.play()
            await wait_for(lambda: (adapter.position_seconds or 0) >= 0.5)
            if paused:
                await controller.pause()
            history = controller.snapshot.recently_played
        finally:
            await controller.close()
            await recording.join_threads()
        with SQLiteStore(database) as store:
            checkpoint = store.checkpoint()
            assert checkpoint is not None
            offset = checkpoint.position_seconds
            assert offset >= 0.5
            assert checkpoint.paused is paused

        restored, adapter, recording = await open_player(
            database, resolver, monkeypatch
        )
        try:
            await restored.restore()
            state = PlaybackState.PAUSED if paused else PlaybackState.PLAYING
            await wait_for(lambda: restored.snapshot.state is state)
            if paused:
                await asyncio.sleep(0.1)
                assert adapter.position_seconds == offset
                await restored.play()
            await wait_for(lambda: (adapter.position_seconds or 0) >= offset + 0.2)
            assert restored.snapshot.recently_played == history
            assert adapter.channel_id == 7
            assert resolver.calls == ["fixture:first", "fixture:first"]
            audible = [f for f in recording.frames if f.position > offset]
            assert audible[0].position == pytest.approx(offset + 0.02)
        finally:
            await restored.close()
            await recording.join_threads()

    asyncio.run(scenario())
