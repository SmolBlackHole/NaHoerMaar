# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""No Discord network, FFmpeg, codecs or real-time output in these adapter tests."""

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from nahormaar_backend.engine import discord as adapter
from nahormaar_backend.engine.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioSourceNotReady,
    AudioEvent,
    AudioStarted,
    CrossfadeCompleted,
    PlayableSource,
    VoiceDisconnected,
)
from nahormaar_backend.engine.domain.catalog import TrackFinding
from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.integrations.audio_mixer import BufferedAudio

from .test_catalog import REFERENCE
from .test_radio_session import until


SOURCE = PlayableSource(
    TrackFinding(REFERENCE, TrackMetadata(duration_seconds=120)),
    "https://stream.invalid/fixture",
)


class Buffer:
    def __init__(self) -> None:
        self.cleaned = False
        self.ready = threading.Event()
        self.ready.set()
        self.waiting = threading.Event()

    def wait_ready(self, frames: int) -> bool:
        self.waiting.set()
        return self.ready.wait(2) and not self.cleaned

    def cleanup(self) -> None:
        self.cleaned = True
        self.ready.set()


class Mixer:
    def __init__(
        self,
        buffer: Buffer,
        *,
        volume: float,
        position: float,
        paused: bool,
        on_started: Callable[[], None],
    ) -> None:
        self.buffer = buffer
        self.volume = volume
        self.position_seconds = position
        self.paused = paused
        self.on_started = on_started
        self.transitioning = False
        self.current_error: Exception | None = None
        self.prepared: Buffer | None = None
        self.tail: Buffer | None = None
        self.on_due: Callable[[], None] = lambda: None
        self.on_faded: Callable[[], None] = lambda: None

    def stage(
        self,
        buffer: Buffer,
        *,
        seconds: float,
        duration: float,
        on_due: Callable[[], None],
    ) -> bool:
        self.prepared, self.on_due = buffer, on_due
        return True

    def activate(
        self,
        on_faded: Callable[[], None],
        on_activate: Callable[[], None],
        on_started: Callable[[], None],
    ) -> bool:
        if self.prepared is None:
            return False
        self.tail, self.buffer, self.prepared = self.buffer, self.prepared, None
        self.position_seconds = 0.02
        self.transitioning = True
        self.on_faded, self.on_started = on_faded, on_started
        on_activate()
        on_started()
        return True

    def discard(self) -> Buffer | None:
        prepared, self.prepared = self.prepared, None
        return prepared

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def cleanup(self) -> None:
        for buffer in (self.buffer, self.prepared, self.tail):
            if buffer:
                buffer.cleanup()


def transport() -> tuple[MagicMock, MagicMock, MagicMock]:
    voice = MagicMock(spec=discord.VoiceClient)
    voice.is_connected.return_value = True
    voice.disconnect = AsyncMock()
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.guild.unavailable = False
    channel.permissions_for.return_value.view_channel = True
    channel.permissions_for.return_value.connect = True
    channel.permissions_for.return_value.speak = True
    channel.connect = AsyncMock(return_value=voice)
    client = MagicMock(spec=discord.Client)
    client.get_channel.return_value = channel
    return client, channel, voice


def test_targeted_resources_and_crossfade_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(adapter, "CrossfadeSource", Mixer)
        buffers: list[Buffer] = []

        def factory(source: PlayableSource, position: float) -> BufferedAudio:
            buffer = Buffer()
            buffers.append(buffer)
            return cast(BufferedAudio, buffer)

        client, _, voice = transport()
        output = adapter.DiscordOutput(
            client, tmp_path / "unused", buffer_factory=factory
        )
        connection, first, second, preparation = uuid4(), uuid4(), uuid4(), uuid4()
        facts: list[AudioEvent] = []
        await output.connect(123, connection)
        await output.disconnect(uuid4())
        assert output.connection and output.connection.connection_id == connection
        await output.play(SOURCE, first, facts.append)
        mixer = cast(Mixer, voice.play.call_args.args[0])
        mixer.on_started()
        mixer.on_started()
        assert facts == [AudioStarted(first, 0)]
        output.pause(uuid4())
        assert output.progress and not output.progress.paused
        output.pause(first)
        assert output.progress and output.progress.paused
        output.resume(first)
        assert await output.prepare_next(
            SOURCE,
            outgoing_attempt_id=first,
            preparation_id=preparation,
            seconds=5,
            notify=facts.append,
        )
        await output.discard_next(uuid4())
        assert not buffers[1].cleaned
        assert not output.start_transition(
            outgoing_attempt_id=uuid4(),
            preparation_id=preparation,
            attempt_id=second,
            notify=facts.append,
        )
        assert output.start_transition(
            outgoing_attempt_id=first,
            preparation_id=preparation,
            attempt_id=second,
            notify=facts.append,
        )
        assert isinstance(facts[-1], AudioStarted) and facts[-1].attempt_id == second
        await output.stop(first)
        await output.discard_next(preparation)
        assert output.progress and output.progress.attempt_id == second
        assert not buffers[1].cleaned
        mixer.on_faded()
        assert facts[-1] == CrossfadeCompleted(second, preparation)
        await output.stop(second)
        assert output.progress is None and all(buffer.cleaned for buffer in buffers)
        assert facts[-1] == AudioCompleted(second, 0.02, AudioEndReason.STOPPED)
        # Discord may report its after-callback after explicit cleanup completed.
        voice.play.call_args.kwargs["after"](None)
        assert len([fact for fact in facts if isinstance(fact, AudioCompleted)]) == 1
        await output.disconnect(connection)
        assert output.connection is None
        await output.close()

    asyncio.run(scenario())


def test_prepares_next_track_without_crossfade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(adapter, "CrossfadeSource", Mixer)
        client, _, voice = transport()
        output = adapter.DiscordOutput(
            client,
            tmp_path / "unused",
            buffer_factory=lambda _source, _position: cast(BufferedAudio, Buffer()),
        )
        attempt, preparation = uuid4(), uuid4()
        await output.connect(123, uuid4())
        await output.play(SOURCE, attempt, lambda _: None)
        mixer = cast(Mixer, voice.play.call_args.args[0])

        assert await output.prepare_next(
            SOURCE,
            outgoing_attempt_id=attempt,
            preparation_id=preparation,
            seconds=0,
            notify=lambda _: None,
        )
        assert mixer.prepared is not None
        await output.close()

    asyncio.run(scenario())


def test_play_waits_for_audio_and_does_not_start_an_empty_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(adapter, "CrossfadeSource", Mixer)
        buffer = Buffer()
        buffer.ready.clear()
        client, _, voice = transport()
        output = adapter.DiscordOutput(
            client,
            tmp_path / "unused",
            buffer_factory=lambda _source, _position: cast(BufferedAudio, buffer),
        )
        await output.connect(123, uuid4())
        pending = asyncio.create_task(output.play(SOURCE, uuid4(), lambda _: None))
        await until(buffer.waiting.is_set)
        voice.play.assert_not_called()
        buffer.ready.set()
        await pending
        voice.play.assert_called_once()
        await output.close()

        class EmptyBuffer(Buffer):
            def wait_ready(self, frames: int) -> bool:
                return False

        empty = EmptyBuffer()
        client, _, voice = transport()
        output = adapter.DiscordOutput(
            client,
            tmp_path / "unused",
            buffer_factory=lambda _source, _position: cast(BufferedAudio, empty),
        )
        await output.connect(123, uuid4())
        with pytest.raises(AudioSourceNotReady, match="initial frame"):
            await output.play(SOURCE, uuid4(), lambda _: None)
        voice.play.assert_not_called()
        assert empty.cleaned and output.progress is None
        await output.close()

    asyncio.run(scenario())


def test_cancelled_buffer_creation_cleans_late_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(adapter, "CrossfadeSource", Mixer)
        started, release = threading.Event(), threading.Event()
        buffer = Buffer()

        def factory(source: PlayableSource, position: float) -> BufferedAudio:
            started.set()
            assert release.wait(2)
            return cast(BufferedAudio, buffer)

        client, _, voice = transport()
        output = adapter.DiscordOutput(
            client, tmp_path / "unused", buffer_factory=factory
        )
        await output.connect(123, uuid4())
        pending = asyncio.create_task(output.play(SOURCE, uuid4(), lambda _: None))
        await until(started.is_set)
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert buffer.cleaned and output.progress is None
        voice.play.assert_not_called()
        await output.close()

    asyncio.run(scenario())


def test_cancelled_join_disconnects_late_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        client, channel, voice = transport()
        started, release = asyncio.Event(), asyncio.Event()

        async def connect(**kwargs: object) -> discord.VoiceClient:
            started.set()
            await release.wait()
            return cast(discord.VoiceClient, voice)

        channel.connect.side_effect = connect
        output = adapter.DiscordOutput(client, tmp_path / "unused")
        pending = asyncio.create_task(output.connect(123, uuid4()))
        await started.wait()
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert output.connection is None
        voice.disconnect.assert_awaited_once_with(force=True)
        await output.close()

    asyncio.run(scenario())


def test_lost_connection_is_visible_before_audio_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(adapter, "CrossfadeSource", Mixer)
        client, _, voice = transport()
        output = adapter.DiscordOutput(
            client,
            tmp_path / "unused",
            buffer_factory=lambda _source, _position: cast(BufferedAudio, Buffer()),
        )
        facts: list[AudioEvent] = []
        losses: list[VoiceDisconnected] = []
        disconnected_during_callback: list[bool] = []

        def notify(fact: AudioEvent) -> None:
            facts.append(fact)
            if isinstance(fact, AudioCompleted):
                disconnected_during_callback.append(output.connection is None)

        output.set_disconnect_handler(losses.append)
        await output.connect(123, uuid4())
        connection = output.connection
        await output.play(SOURCE, uuid4(), notify, position_seconds=47)
        voice.is_connected.return_value = False
        await until(lambda: bool(losses))
        assert disconnected_during_callback == [True]
        assert losses[0].connection == connection
        assert losses[0].progress and losses[0].progress.position_seconds == 47
        await output.close()

    asyncio.run(scenario())


def test_cancelled_preparation_releases_buffer_without_stopping_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(adapter, "CrossfadeSource", Mixer)
        buffers = [Buffer(), Buffer()]
        buffers[1].ready.clear()
        candidates = iter(buffers)
        client, _, _ = transport()
        output = adapter.DiscordOutput(
            client,
            tmp_path / "unused",
            buffer_factory=lambda _source, _position: cast(
                BufferedAudio, next(candidates)
            ),
        )
        await output.connect(123, uuid4())
        attempt = uuid4()
        await output.play(SOURCE, attempt, lambda _: None)
        pending = asyncio.create_task(
            output.prepare_next(
                SOURCE,
                outgoing_attempt_id=attempt,
                preparation_id=uuid4(),
                seconds=5,
                notify=lambda _: None,
            )
        )
        await until(buffers[1].waiting.is_set)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert buffers[1].cleaned and not buffers[0].cleaned
        assert output.progress and output.progress.attempt_id == attempt
        await output.close()
        assert buffers[0].cleaned

    asyncio.run(scenario())
