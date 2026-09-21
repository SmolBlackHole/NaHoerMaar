# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Correlated Discord transport and audio output, without playback policy.

Reuse the audited FFmpeg/buffer/mixer mechanisms directly. Every destructive
operation names its resource; an old callback cannot stop a newer connection,
attempt or prepared source. The composition root owns the Discord client.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from uuid import UUID

import discord

from ..integrations.audio_mixer import FRAME_SECONDS, BufferedAudio, CrossfadeSource
from ..integrations.audio_sources import FFmpegSource, MediaStreamError, VolumeSource
from .audio import (
    AudioCompleted,
    AudioEndReason,
    AudioError,
    AudioEvent,
    AudioProgress,
    AudioStarted,
    CrossfadeCompleted,
    CrossfadeDue,
    PlayableSource,
    VoiceChannel,
    VoiceConnection,
    VoiceDisconnected,
    VoiceError,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Attempt:
    id: UUID
    source: PlayableSource
    notify: Callable[[AudioEvent], None]
    started: bool = False


@dataclass(slots=True)
class _Output:
    mixer: CrossfadeSource
    attempt: _Attempt
    lock: threading.RLock = field(default_factory=threading.RLock)
    activating: bool = False
    activation_done: threading.Event = field(default_factory=threading.Event)
    finishing: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    forced: AudioEndReason | None = None
    cleanup_error: AudioError | None = None

    def __post_init__(self) -> None:
        self.activation_done.set()


@dataclass(slots=True)
class _Prepared:
    id: UUID
    output: _Output
    source: PlayableSource
    buffer: BufferedAudio
    staged: bool = False


class DiscordOutput:
    """One AudioPlayer + VoiceTransport; shared instance, closed once by runtime."""

    def __init__(
        self,
        client: discord.Client,
        ffmpeg_path: Path,
        *,
        buffer_factory: Callable[[PlayableSource, float], BufferedAudio] | None = None,
    ) -> None:
        self._client, self._ffmpeg = client, ffmpeg_path
        self._make_buffer = buffer_factory or self._buffer
        self._voice: discord.VoiceClient | None = None
        self._connection: VoiceConnection | None = None
        self._output: _Output | None = None
        self._prepared: _Prepared | None = None
        self._volume = 1.0
        self._voice_lock = asyncio.Lock()
        self._audio_lock = asyncio.Lock()
        self._monitor: asyncio.Task[None] | None = None
        self._disconnect_handler: Callable[[VoiceDisconnected], None] = lambda _: None
        self._closing = False

    @property
    def connection(self) -> VoiceConnection | None:
        return self._connection if self._voice and self._voice.is_connected() else None

    @property
    def progress(self) -> AudioProgress | None:
        output = self._output
        if output is None:
            return None
        # Avoid taking the mixer and adapter locks in opposite order to activate.
        while True:
            with output.lock:
                attempt = output.attempt
            position, paused, fading = (
                output.mixer.position_seconds,
                output.mixer.paused,
                output.mixer.transitioning,
            )
            with output.lock:
                if output.attempt is attempt:
                    return AudioProgress(attempt.id, position, paused, fading)

    def channels(self) -> tuple[VoiceChannel, ...]:
        return tuple(
            VoiceChannel(
                channel.id,
                channel.name,
                guild.id,
                guild.name,
                channel.permissions_for(guild.me).view_channel
                and channel.permissions_for(guild.me).connect,
                channel.permissions_for(guild.me).speak,
            )
            for guild in sorted(
                self._client.guilds, key=lambda item: (item.name.casefold(), item.id)
            )
            if not guild.unavailable
            for channel in guild.voice_channels
        )

    def set_disconnect_handler(
        self, handler: Callable[[VoiceDisconnected], None]
    ) -> None:
        self._disconnect_handler = handler

    async def connect(self, channel_id: int, connection_id: UUID) -> None:
        async with self._voice_lock:
            if self._closing:
                raise VoiceError("Voice output is closing.")
            if self._connection is not None:
                if self.connection == VoiceConnection(connection_id, channel_id):
                    return
                raise VoiceError("Disconnect the previous connection before joining.")
            channel = self._client.get_channel(channel_id)
            if (
                not isinstance(channel, discord.VoiceChannel)
                or channel.guild.unavailable
            ):
                raise VoiceError("Voice channel is unavailable.")
            permissions = channel.permissions_for(channel.guild.me)
            if not (
                permissions.view_channel and permissions.connect and permissions.speak
            ):
                raise VoiceError("Voice channel permissions are missing.")
            # Shield the connection establishment so cancellation can close a
            # transport returned just as the caller stops waiting for it.
            pending: asyncio.Task[discord.VoiceClient] = asyncio.create_task(
                channel.connect(reconnect=False, timeout=30.0, self_deaf=True)
            )
            try:
                voice = await asyncio.shield(pending)
            except asyncio.CancelledError:
                try:
                    voice = await pending
                except Exception:
                    _LOGGER.debug("engine.voice.cancelled_join_failed")
                else:
                    await voice.disconnect(force=True)
                raise
            except Exception as exc:
                raise VoiceError("Discord voice connection failed.") from exc
            self._voice, self._connection = (
                voice,
                VoiceConnection(connection_id, channel_id),
            )
            self._monitor = asyncio.create_task(
                self._watch(self._connection), name="engine-voice-monitor"
            )

    async def disconnect(self, connection_id: UUID) -> None:
        async with self._voice_lock:
            if self._connection and self._connection.connection_id == connection_id:
                await self._disconnect(lost=False)

    async def _disconnect(self, *, lost: bool) -> None:
        voice, connection, progress = self._voice, self._connection, self.progress
        self._voice, self._connection = None, None
        monitor, self._monitor = self._monitor, None
        if monitor and monitor is not asyncio.current_task():
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        try:
            if progress:
                if voice:
                    voice.stop()
                await self.stop(progress.attempt_id)
        finally:
            try:
                if voice:
                    await voice.disconnect(force=True)
            finally:
                if lost and connection:
                    self._disconnect_handler(VoiceDisconnected(connection, progress))

    async def _watch(self, connection: VoiceConnection) -> None:
        while True:
            await asyncio.sleep(0.25)
            if self._connection != connection:
                return
            if self.connection is None:
                async with self._voice_lock:
                    if self._connection == connection:
                        await self._disconnect(lost=True)
                return

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Gateway events have no attempt ID. An old channel-leave notification
        # is not evidence that our current VoiceClient has disconnected.
        if (
            self._client.user
            and member.id == self._client.user.id
            and after.channel is None
        ):
            async with self._voice_lock:
                if self._connection and self.connection is None:
                    await self._disconnect(lost=True)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        async with self._voice_lock:
            if self._voice and self._voice.guild.id == guild.id:
                await self._disconnect(lost=True)

    def _buffer(self, source: PlayableSource, position: float) -> BufferedAudio:
        raw = FFmpegSource(
            self._ffmpeg,
            source.stream_url,
            source.headers,
            opus=source.is_opus,
            position_seconds=position,
            duration_seconds=source.track.metadata.duration_seconds,
        )
        try:
            return BufferedAudio(VolumeSource(raw))
        except BaseException:
            raw.cleanup()
            raise

    async def _create_buffer(
        self, source: PlayableSource, position: float = 0
    ) -> BufferedAudio:
        pending = asyncio.create_task(
            asyncio.to_thread(self._make_buffer, source, position)
        )
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            try:
                audio = await pending
            except Exception:
                _LOGGER.debug("engine.audio.cancelled_buffer_creation_failed")
            else:
                await asyncio.to_thread(audio.cleanup)
            raise

    async def play(
        self,
        source: PlayableSource,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None:
        async with self._audio_lock:
            voice = self._voice
            if self._closing or voice is None or self.connection is None:
                raise VoiceError("Voice is not connected.")
            if self._output:
                raise AudioError("Previous output must be stopped first.")
            buffer = await self._create_buffer(source, position_seconds)
            attempt = _Attempt(attempt_id, source, notify)
            try:
                output = _Output(
                    CrossfadeSource(
                        buffer,
                        volume=self._volume,
                        position=position_seconds,
                        paused=paused,
                        on_started=lambda: self._started(output, attempt),
                    ),
                    attempt,
                )
                if voice is not self._voice or not voice.is_connected():
                    raise VoiceError("Connection changed while preparing output.")
                self._output = output
                voice.play(
                    output.mixer, after=lambda error: self._completed(output, error)
                )
                if paused:
                    voice.pause()
            except BaseException:
                voice.stop()
                self._output = None
                await asyncio.to_thread(buffer.cleanup)
                raise

    def _started(self, output: _Output, attempt: _Attempt) -> None:
        position = output.mixer.position_seconds
        with output.lock:
            if (
                self._output is not output
                or output.attempt is not attempt
                or attempt.started
                or output.finishing
            ):
                return
            attempt.started = True
        attempt.notify(AudioStarted(attempt.id, position))

    def _completed(self, output: _Output, error: Exception | None) -> None:
        # Audio-thread completion and event-loop stop may race with activation.
        while True:
            with output.lock:
                finishing, activating, attempt = (
                    output.finishing,
                    output.activating,
                    output.attempt,
                )
            if finishing:
                output.finished.wait()
                return
            if activating:
                output.activation_done.wait()
                continue
            position, terminal_error = (
                output.mixer.position_seconds,
                output.mixer.current_error,
            )
            with output.lock:
                if (
                    output.finishing
                    or output.activating
                    or output.attempt is not attempt
                ):
                    continue
                output.finishing = True
                break
        try:
            try:
                output.mixer.cleanup()
            except Exception:
                output.cleanup_error = AudioError("Audio cleanup failed.")
            duration = attempt.source.track.metadata.duration_seconds
            if output.forced:
                reason = output.forced
            elif isinstance(terminal_error, MediaStreamError) or isinstance(
                error, MediaStreamError
            ):
                reason = AudioEndReason.INTERRUPTED
            elif terminal_error or error or output.cleanup_error:
                reason = AudioEndReason.OUTPUT_FAILED
            elif duration is not None and position + 1 < duration:
                reason = AudioEndReason.INTERRUPTED
            else:
                reason = AudioEndReason.NATURAL
            if self._output is output:
                self._output = None
            _LOGGER.info(
                "engine.audio.completed attempt=%s position=%.3f reason=%s",
                attempt.id,
                position,
                reason,
            )
            attempt.notify(
                AudioCompleted(
                    attempt.id,
                    position,
                    reason,
                    AudioError("Audio output failed.")
                    if error or terminal_error or output.cleanup_error
                    else None,
                )
            )
        finally:
            output.finished.set()

    async def stop(self, attempt_id: UUID) -> None:
        async with self._audio_lock:
            output = self._output
            if output is None or output.attempt.id != attempt_id:
                return
            with output.lock:
                output.forced = AudioEndReason.STOPPED
            if self._voice:
                self._voice.stop()
            cleanup = asyncio.create_task(
                asyncio.to_thread(self._completed, output, None)
            )
            try:
                await asyncio.shield(cleanup)
            finally:
                await cleanup
                if self._prepared and self._prepared.output is output:
                    await self.discard_next(self._prepared.id)
            if output.cleanup_error:
                raise output.cleanup_error

    def pause(self, attempt_id: UUID) -> None:
        if (
            self._output
            and self._output.attempt.id == attempt_id
            and not self._output.mixer.paused
        ):
            self._output.mixer.pause()
            if self._voice:
                self._voice.pause()

    def resume(self, attempt_id: UUID) -> None:
        if (
            self._output
            and self._output.attempt.id == attempt_id
            and self._output.mixer.paused
        ):
            self._output.mixer.resume()
            if self._voice:
                self._voice.resume()

    def set_volume(self, volume: float) -> None:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")
        self._volume = volume
        if self._output:
            self._output.mixer.volume = volume

    async def prepare_next(
        self,
        source: PlayableSource,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        seconds: float,
        notify: Callable[[AudioEvent], None],
    ) -> bool:
        output = self._output
        if (
            output is None
            or output.attempt.id != outgoing_attempt_id
            or self._prepared
            or self.connection is None
            or output.mixer.transitioning
        ):
            return False
        duration = output.attempt.source.track.metadata.duration_seconds
        if duration is None or not isfinite(seconds) or seconds <= 0:
            return False
        audio = await self._create_buffer(source)
        prepared = _Prepared(preparation_id, output, source, audio)
        self._prepared = prepared
        try:
            ready = await asyncio.to_thread(
                audio.wait_ready, max(1, round(seconds / FRAME_SECONDS))
            )
            if (
                ready
                and self._output is output
                and output.attempt.id == outgoing_attempt_id
                and self._prepared is prepared
                and not output.finishing
            ):
                prepared.staged = output.mixer.stage(
                    audio,
                    seconds=seconds,
                    duration=duration,
                    on_due=lambda: notify(
                        CrossfadeDue(
                            outgoing_attempt_id,
                            preparation_id,
                            source.track.metadata.duration_seconds,
                        )
                    ),
                )
            return prepared.staged
        finally:
            if not prepared.staged:
                await asyncio.to_thread(audio.cleanup)
                if self._prepared is prepared:
                    self._prepared = None

    async def discard_next(self, preparation_id: UUID) -> None:
        prepared = self._prepared
        if prepared is None or prepared.id != preparation_id:
            return
        self._prepared = None
        if prepared.staged:
            discarded = prepared.output.mixer.discard()
            if discarded is None:
                return  # Activation/retirement now owns this buffer.
        cleanup = asyncio.create_task(asyncio.to_thread(prepared.buffer.cleanup))
        try:
            await asyncio.shield(cleanup)
        finally:
            await cleanup

    def start_transition(
        self,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
    ) -> bool:
        output, prepared = self._output, self._prepared
        if (
            output is None
            or prepared is None
            or prepared.id != preparation_id
            or prepared.output is not output
            or not prepared.staged
        ):
            return False
        with output.lock:
            if (
                output.attempt.id != outgoing_attempt_id
                or output.finishing
                or output.activating
            ):
                return False
            output.activating = True
            output.activation_done.clear()
        attempt = _Attempt(attempt_id, prepared.source, notify)

        def activated() -> None:
            with output.lock:
                output.attempt = attempt

        try:
            accepted = output.mixer.activate(
                lambda: notify(CrossfadeCompleted(attempt_id, preparation_id)),
                activated,
                lambda: self._started(output, attempt),
            )
            if accepted:
                self._prepared = None
            return accepted
        finally:
            with output.lock:
                output.activating = False
                output.activation_done.set()

    async def close(self) -> None:
        self._closing = True
        async with self._voice_lock:
            await self._disconnect(lost=False)
        if progress := self.progress:
            await self.stop(progress.attempt_id)
        if self._prepared:
            await self.discard_next(self._prepared.id)
