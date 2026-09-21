# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Opt-in local recording acceptance. Never uses a bot token or Discord sockets.

Run only in the coordinated audio-test window with NAHORMAAR_ENGINE_AUDIO_TESTS=1.
The normal engine suite explicitly skips these CPU/real-time checks.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import audioop
import json
import math
import os
import struct
import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import discord
import imageio_ffmpeg  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
import pytest
from discord.opus import OPUS_SILENCE
from discord.player import AudioPlayer

from nahormaar_backend.engine.audio import PlayableSource
from nahormaar_backend.engine.catalog import Catalog
from nahormaar_backend.engine.discord import DiscordOutput
from nahormaar_backend.engine.domain.catalog import (
    MediaKind,
    MediaReference,
    TrackFinding,
)
from nahormaar_backend.engine.domain.metadata import (
    MetadataKind,
    MetadataSource,
    TrackMetadata,
)
from nahormaar_backend.engine.domain.playback import (
    Control,
    Join,
    Seek,
    SetCrossfade,
    SetVolume,
)
from nahormaar_backend.engine.domain.queue import Add
from nahormaar_backend.engine.domain.sessions import PlaybackIntent, PlaybackPhase
from nahormaar_backend.engine.domain.tracks import MediaIdentity
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.session import Session
from nahormaar_backend.engine.youtube import YouTubeProvider
from nahormaar_backend.integrations.audio_mixer import CrossfadeSource
from nahormaar_backend.integrations.audio_sources import AudioFrame, FrameEncoder

from .database import isolated_database
from .test_catalog import TIME
from .test_discord_output import transport

pytestmark = pytest.mark.skipif(
    os.environ.get("NAHORMAAR_ENGINE_AUDIO_TESTS") != "1",
    reason="Requires separately coordinated local audio-test window.",
)


@dataclass(frozen=True)
class Frame:
    packet: bytes
    pcm: bytes
    position: float
    fading: bool
    volume: float


class RecordingEncoder(FrameEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.pcm = b""

    def encode(self, frame: AudioFrame, volume: float) -> bytes:
        self.pcm = audioop.mul(frame.pcm, 2, volume)
        return super().encode(frame, volume)


class RecordingVoice:
    """discord.py audio timing, with packets recorded to memory instead of sent."""

    def __init__(self) -> None:
        self.client = SimpleNamespace(loop=asyncio.get_running_loop())
        self.ws = self
        self.connected = True
        self.frames: list[Frame] = []
        self.players: list[AudioPlayer] = []
        self.source: CrossfadeSource | None = None
        self.encoder = RecordingEncoder()

    async def speak(self, speaking: object) -> None:
        pass

    def is_connected(self) -> bool:
        return self.connected

    def send_audio_packet(self, packet: bytes, *, encode: bool) -> None:
        assert not encode and self.source is not None
        if packet != OPUS_SILENCE:
            self.frames.append(
                Frame(
                    packet,
                    self.encoder.pcm,
                    self.source.position_seconds,
                    self.source.transitioning,
                    self.source.volume,
                )
            )

    def play(
        self, source: discord.AudioSource, *, after: Callable[[Exception | None], None]
    ) -> None:
        self.source = cast(CrossfadeSource, source)
        self.source._encoder = self.encoder
        player = AudioPlayer(source, cast(discord.VoiceClient, self), after=after)
        self.players.append(player)
        player.start()

    def stop(self) -> None:
        if self.players:
            self.players[-1].stop()

    def pause(self) -> None:
        if self.players:
            self.players[-1].pause()

    def resume(self) -> None:
        if self.players and self.players[-1].is_paused():
            self.players[-1].resume()

    async def disconnect(self, *, force: bool = False) -> None:
        self.connected = False
        self.stop()

    async def settle(self) -> None:
        for player in self.players:
            await asyncio.to_thread(player.join, 3)
            assert not player.is_alive() and player._current_error is None


class LocalProvider(YouTubeProvider):
    def __init__(self, sources: tuple[PlayableSource, ...]) -> None:
        super().__init__(Path("unused"))
        self.sources = {source.track.reference.identity: source for source in sources}
        self.calls = 0

    async def resolve_audio(self, identity: MediaIdentity) -> PlayableSource:
        self.calls += 1
        return self.sources[identity]


def tone(
    folder: Path, frequency: int, seconds: int, opus: bool, ffmpeg: Path
) -> PlayableSource:
    path = folder / f"tone-{frequency}.wav"
    second = b"".join(
        struct.pack("<hh", sample, sample)
        for index in range(48000)
        for sample in (int(10000 * math.sin(2 * math.pi * frequency * index / 48000)),)
    )
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(second * seconds)
    if opus:
        encoded = path.with_suffix(".webm")
        subprocess.run(  # noqa: S603 - local generated files and packaged executable
            [
                str(ffmpeg),
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
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        path = encoded
    identity = MediaIdentity("youtube", f"tone{frequency:07d}")
    return PlayableSource(
        TrackFinding(
            MediaReference(
                identity,
                MediaKind.TRACK,
                f"https://youtube.com/watch?v={identity.external_id}",
            ),
            TrackMetadata(title=f"Tone {frequency}", duration_seconds=seconds),
        ),
        str(path),
        is_opus=opus,
    )


async def wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(45):
        while not predicate():
            await asyncio.sleep(0.01)


def amplitude(pcm: bytes, frequency: int) -> float:
    coefficient = 2 * math.cos(2 * math.pi * frequency / 48000)
    previous = before = 0.0
    count = 0
    for left, _ in struct.iter_unpack("<hh", pcm):
        current = left + coefficient * previous - before
        before, previous = previous, current
        count += 1
    power = previous * previous + before * before - coefficient * previous * before
    return 2 * math.sqrt(max(0, power)) / count


@pytest.mark.parametrize(
    ("opus", "fade", "controls"),
    [(False, 3, False), (True, 3, False), (True, 5, False), (True, 5, True)],
)
def test_recorded_crossfade_overlap_timing_and_controls(
    tmp_path: Path, opus: bool, fade: int, controls: bool
) -> None:
    async def scenario() -> None:
        ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
        sources = tuple(
            tone(tmp_path, frequency, 2 * fade + 2, opus, ffmpeg)
            for frequency in (400, 1000)
        )
        provider = LocalProvider(sources)
        async with isolated_database(tmp_path / "audio.sqlite3") as sessions:
            metadata = MetadataStore(sessions, clock=lambda: TIME)
            tracks = await metadata.remember(
                tuple(source.track for source in sources),
                source=MetadataSource("fixture", MetadataKind.DETAIL, TIME),
            )
            catalog = Catalog((provider,), metadata, clock=lambda: TIME)
            client, channel, _ = transport()
            recording = RecordingVoice()
            channel.connect.return_value = recording
            output = DiscordOutput(client, ffmpeg)
            owner = await Session.open(
                sessions,
                uuid4(),
                clock=lambda: TIME,
                catalog=catalog,
                audio=output,
                voice=output,
            )
            try:
                await owner.request(uuid4(), Add(tuple(track.id for track in tracks)))
                await owner.request(uuid4(), SetCrossfade(fade))
                await owner.request(uuid4(), Join(123))
                await wait_for(lambda: len(owner.snapshot.history) == 2)
                if controls:
                    await wait_for(
                        lambda: (
                            output.progress is not None
                            and output.progress.position_seconds >= 1
                        )
                    )
                    await owner.request(uuid4(), Control.PAUSE)
                    frozen = output.progress
                    await owner.request(uuid4(), SetVolume(0.4))
                    await asyncio.sleep(0.15)
                    assert output.progress == frozen
                    await owner.request(uuid4(), Control.PLAY)
                await wait_for(
                    lambda: owner.snapshot.playback.phase is PlaybackPhase.IDLE
                )
                assert owner.snapshot.playback.error is None
                assert len(recording.players) == 1 and provider.calls == 2
            finally:
                await owner.close()
                await output.close()
                await recording.settle()
                await catalog.close()
            decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
            decoded = [
                decoder.decode(frame.packet, fec=False) for frame in recording.frames
            ]
            with wave.open(str(tmp_path / "crossfade.wav"), "wb") as result:
                result.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                result.writeframes(b"".join(decoded))
            mixed = [
                ((frame.pcm if controls else pcm), frame)
                for pcm, frame in zip(decoded, recording.frames, strict=True)
                if frame.fading and 0.2 < frame.position < fade - 0.2
            ]
            levels = [
                (
                    amplitude(pcm, 400) / frame.volume,
                    amplitude(pcm, 1000) / frame.volume,
                )
                for pcm, frame in mixed
            ]
            assert len(levels) >= (fade - 0.5) * 50
            assert levels[0][0] > 9000 and levels[0][1] < 1000
            assert levels[-1][0] < 1000 and levels[-1][1] > 9000
            assert sum(left > 2500 and right > 2500 for left, right in levels) >= 45
            assert all(8500 < left + right < 11500 for left, right in levels)
            (tmp_path / "measurement.json").write_text(
                json.dumps(
                    {
                        "fade_seconds": fade,
                        "opus_input": opus,
                        "controls_during_fade": controls,
                        "audio_threads": len(recording.players),
                        "overlap_frames": len(levels),
                        "first_levels": levels[0],
                        "last_levels": levels[-1],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("paused", [False, True])
def test_recorded_seek_reconnect_and_restart_keep_logical_play(
    tmp_path: Path, paused: bool
) -> None:
    async def scenario() -> None:
        ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
        source = tone(tmp_path, 400, 12, False, ffmpeg)
        provider = LocalProvider((source,))
        async with isolated_database(tmp_path / "recovery.sqlite3") as sessions:
            metadata = MetadataStore(sessions, clock=lambda: TIME)
            (track,) = await metadata.remember(
                (source.track,),
                source=MetadataSource("fixture", MetadataKind.DETAIL, TIME),
            )
            catalog = Catalog((provider,), metadata, clock=lambda: TIME)
            client, channel, _ = transport()
            recording = RecordingVoice()
            channel.connect.return_value = recording
            output = DiscordOutput(client, ffmpeg)
            identifier = uuid4()
            owner = await Session.open(
                sessions,
                identifier,
                clock=lambda: TIME,
                catalog=catalog,
                audio=output,
                voice=output,
            )
            try:
                await owner.request(uuid4(), Add((track.id,)))
                await owner.request(uuid4(), Join(123))
                await wait_for(lambda: bool(owner.snapshot.history))
                play_id = owner.snapshot.checkpoint.play_id
                await owner.request(uuid4(), Seek(4))
                await wait_for(
                    lambda: (
                        output.progress is not None
                        and output.progress.position_seconds >= 4.2
                    )
                )
                if paused:
                    await owner.request(uuid4(), Control.PAUSE)
                await owner.close()
                saved = owner.snapshot.checkpoint
                await recording.settle()
                restored = RecordingVoice()
                next_voice = RecordingVoice()
                channel.connect.return_value = restored
                owner = await Session.open(
                    sessions,
                    identifier,
                    clock=lambda: TIME,
                    catalog=catalog,
                    audio=output,
                    voice=output,
                )
                try:
                    await wait_for(lambda: output.progress is not None)
                    progress = output.progress
                    assert (
                        progress
                        and abs(progress.position_seconds - saved.position_seconds)
                        < 0.15
                    )
                    assert progress.paused is paused
                    assert (
                        owner.snapshot.checkpoint.play_id == play_id
                        and len(owner.snapshot.history) == 1
                    )
                    assert owner.snapshot.checkpoint.intent is (
                        PlaybackIntent.PAUSED if paused else PlaybackIntent.PLAYING
                    )
                    # Explicit reconnect to another fixture channel also preserves the play.
                    channel.connect.return_value = next_voice
                    await owner.request(uuid4(), Join(456))
                    await wait_for(
                        lambda: (
                            output.connection is not None
                            and output.connection.channel_id == 456
                            and output.progress is not None
                        )
                    )
                    assert (
                        owner.snapshot.checkpoint.play_id == play_id
                        and len(owner.snapshot.history) == 1
                    )
                finally:
                    await owner.close()
                    await restored.settle()
                    await next_voice.settle()
            finally:
                await owner.close()
                await output.close()
                await recording.settle()
                await catalog.close()

    asyncio.run(scenario())
