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
import importlib.util
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import cast
from uuid import UUID

import discord

from ..integrations.audio_mixer import (
    FRAME_SECONDS,
    AudioDiagnostics,
    BufferedAudio,
    CrossfadeSource,
)
from ..integrations.audio_sources import FFmpegSource, MediaStreamError, VolumeSource
from .audio import (
    AudioCompleted,
    AudioEndReason,
    AudioError,
    AudioSourceNotReady,
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
from .logs import current_actor, current_trace_id, log_context, request_trace_id

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Attempt:
    id: UUID
    source: PlayableSource
    notify: Callable[[AudioEvent], None]
    trace_id: str
    actor_id: str | None
    actor_name: str | None
    created_at: float
    start_mode: str
    audio_pid: int | None = None
    first_frame_seconds: float | None = None
    preparation_id: UUID | None = None
    started: bool = False
    started_at: float | None = None
    summarized: bool = False
    summary_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True, slots=True)
class _RetiringAttempt:
    attempt: _Attempt
    position: float
    diagnostics: AudioDiagnostics


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
    retiring: _RetiringAttempt | None = None

    def __post_init__(self) -> None:
        self.activation_done.set()


@dataclass(slots=True)
class _Prepared:
    id: UUID
    output: _Output
    source: PlayableSource
    buffer: BufferedAudio
    staged: bool = False
    created_at: float = field(default_factory=time.monotonic)
    crossfade_seconds: float = 0


class DiscordOutput:
    """One AudioPlayer + VoiceTransport; shared instance, closed once by runtime."""

    @staticmethod
    def validate_dependencies() -> None:
        if importlib.util.find_spec("davey") is None:
            raise VoiceError("Discord DAVE voice support is unavailable.")
        try:
            discord.opus.Encoder()
        except (discord.opus.OpusNotLoaded, OSError) as error:
            raise VoiceError("Discord Opus encoding support is unavailable.") from error

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
            _LOGGER.info(
                "engine.voice.connected connection=%s channel_id=%s",
                connection_id,
                channel_id,
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
                if connection:
                    _LOGGER.info(
                        "engine.voice.disconnected connection=%s lost=%s position=%s",
                        connection.connection_id,
                        lost,
                        progress.position_seconds if progress else None,
                    )
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

    @staticmethod
    def _diagnostics(mixer: CrossfadeSource, attempt: _Attempt) -> AudioDiagnostics:
        diagnostics = getattr(mixer, "diagnostics", None)
        return (
            diagnostics
            if isinstance(diagnostics, AudioDiagnostics)
            else AudioDiagnostics(first_frame_seconds=attempt.first_frame_seconds)
        )

    @staticmethod
    def _log_attempt_summary(
        attempt: _Attempt,
        *,
        position: float,
        reason: AudioEndReason | str,
        diagnostics: AudioDiagnostics,
        error: BaseException | None = None,
    ) -> None:
        with attempt.summary_lock:
            if attempt.summarized:
                return
            attempt.summarized = True
        now = time.monotonic()
        with log_context(
            attempt.actor_id,
            attempt.actor_name,
            trace_id=attempt.trace_id,
        ):
            _LOGGER.info(
                "engine.audio.attempt_summary attempt=%s preparation=%s "
                "provider=%s media_id=%s start_mode=%s audio_pid=%s "
                "first_frame_seconds=%s start_seconds=%s attempt_seconds=%.3f "
                "position=%.3f underruns=%s stalled_seconds=%.3f "
                "max_stall_seconds=%.3f reason=%s error=%s",
                attempt.id,
                attempt.preparation_id,
                attempt.source.track.reference.identity.namespace,
                attempt.source.track.reference.identity.external_id,
                attempt.start_mode,
                attempt.audio_pid,
                (
                    f"{diagnostics.first_frame_seconds:.3f}"
                    if diagnostics.first_frame_seconds is not None
                    else None
                ),
                (
                    f"{attempt.started_at - attempt.created_at:.3f}"
                    if attempt.started_at is not None
                    else None
                ),
                now - attempt.created_at,
                position,
                diagnostics.underrun_count,
                diagnostics.stalled_seconds,
                diagnostics.max_stall_seconds,
                reason,
                type(error).__name__ if error is not None else None,
            )

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
            started_at = time.monotonic()
            _LOGGER.info(
                "engine.audio.play_requested attempt=%s provider=%s media_id=%s "
                "position=%.3f paused=%s opus=%s",
                attempt_id,
                source.track.reference.identity.namespace,
                source.track.reference.identity.external_id,
                position_seconds,
                paused,
                source.is_opus,
            )
            voice = self._voice
            if self._closing or voice is None or self.connection is None:
                raise VoiceError("Voice is not connected.")
            if self._output:
                raise AudioError("Previous output must be stopped first.")
            buffer = await self._create_buffer(source, position_seconds)
            actor_id, actor_name = current_actor()
            attempt = _Attempt(
                id=attempt_id,
                source=source,
                notify=notify,
                trace_id=current_trace_id() or request_trace_id(),
                actor_id=actor_id,
                actor_name=actor_name,
                created_at=started_at,
                start_mode="normal",
                audio_pid=getattr(buffer, "process_id", None),
                first_frame_seconds=getattr(buffer, "first_frame_seconds", None),
            )
            try:
                ready_started = time.monotonic()
                if not await asyncio.to_thread(buffer.wait_ready, 1):
                    raise AudioSourceNotReady(
                        "Audio source did not produce an initial frame."
                    )
                _LOGGER.info(
                    "engine.audio.buffer_ready attempt=%s audio_pid=%s "
                    "position=%.3f elapsed=%.3f",
                    attempt_id,
                    getattr(buffer, "process_id", None),
                    position_seconds,
                    time.monotonic() - ready_started,
                )
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
                _LOGGER.debug(
                    "engine.audio.output_submitted attempt=%s elapsed=%.3f",
                    attempt_id,
                    time.monotonic() - started_at,
                )
            except BaseException as error:
                _LOGGER.warning(
                    "engine.audio.play_failed attempt=%s elapsed=%.3f",
                    attempt_id,
                    time.monotonic() - started_at,
                )
                if self._output is not None:
                    voice.stop()
                    self._output = None
                await asyncio.to_thread(buffer.cleanup)
                self._log_attempt_summary(
                    attempt,
                    position=position_seconds,
                    reason="start_failed",
                    diagnostics=AudioDiagnostics(
                        first_frame_seconds=getattr(buffer, "first_frame_seconds", None)
                    ),
                    error=error,
                )
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
            attempt.started_at = time.monotonic()
        with log_context(
            attempt.actor_id,
            attempt.actor_name,
            trace_id=attempt.trace_id,
        ):
            _LOGGER.info(
                "engine.audio.started attempt=%s position=%.3f", attempt.id, position
            )
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
            position, terminal_error, diagnostics = (
                output.mixer.position_seconds,
                output.mixer.current_error,
                self._diagnostics(output.mixer, attempt),
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
            except Exception as cleanup_failure:
                output.cleanup_error = AudioError("Audio cleanup failed.")
                with log_context(
                    attempt.actor_id,
                    attempt.actor_name,
                    trace_id=attempt.trace_id,
                ):
                    _LOGGER.error(
                        "engine.audio.cleanup_failed attempt=%s",
                        attempt.id,
                        exc_info=cleanup_failure,
                    )
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
            with output.lock:
                retiring, output.retiring = output.retiring, None
            if retiring is not None:
                self._log_attempt_summary(
                    retiring.attempt,
                    position=retiring.position,
                    reason=reason,
                    diagnostics=retiring.diagnostics,
                    error=error or terminal_error or output.cleanup_error,
                )
            failure = error or terminal_error or output.cleanup_error
            unexpected = error or (
                terminal_error
                if terminal_error is not None
                and not isinstance(terminal_error, MediaStreamError)
                else None
            )
            if unexpected is not None:
                with log_context(
                    attempt.actor_id,
                    attempt.actor_name,
                    trace_id=attempt.trace_id,
                ):
                    _LOGGER.error(
                        "engine.audio.output_failed attempt=%s",
                        attempt.id,
                        exc_info=unexpected,
                    )
            self._log_attempt_summary(
                attempt,
                position=position,
                reason=reason,
                diagnostics=diagnostics,
                error=failure,
            )
            with log_context(
                attempt.actor_id,
                attempt.actor_name,
                trace_id=attempt.trace_id,
            ):
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
                        AudioError("Audio output failed.") if failure else None,
                    )
                )
        finally:
            output.finished.set()

    async def stop(self, attempt_id: UUID) -> None:
        async with self._audio_lock:
            output = self._output
            if output is None or output.attempt.id != attempt_id:
                _LOGGER.debug(
                    "engine.audio.stop_ignored attempt=%s current_attempt=%s",
                    attempt_id,
                    output.attempt.id if output else None,
                )
                return
            _LOGGER.info(
                "engine.audio.stop_requested attempt=%s position=%.3f",
                attempt_id,
                output.mixer.position_seconds,
            )
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
            _LOGGER.info("engine.audio.stop_completed attempt=%s", attempt_id)

    def pause(self, attempt_id: UUID) -> None:
        if (
            self._output
            and self._output.attempt.id == attempt_id
            and not self._output.mixer.paused
        ):
            self._output.mixer.pause()
            if self._voice:
                self._voice.pause()
            _LOGGER.info(
                "engine.audio.paused attempt=%s position=%.3f",
                attempt_id,
                self._output.mixer.position_seconds,
            )

    def resume(self, attempt_id: UUID) -> None:
        if (
            self._output
            and self._output.attempt.id == attempt_id
            and self._output.mixer.paused
        ):
            self._output.mixer.resume()
            if self._voice:
                self._voice.resume()
            _LOGGER.info(
                "engine.audio.resumed attempt=%s position=%.3f",
                attempt_id,
                self._output.mixer.position_seconds,
            )

    def set_volume(self, volume: float) -> None:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")
        self._volume = volume
        if self._output:
            self._output.mixer.volume = volume
        _LOGGER.debug("engine.audio.volume_set volume=%.3f", volume)

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
        reason = (
            "no_output"
            if output is None
            else "attempt_mismatch"
            if output.attempt.id != outgoing_attempt_id
            else "already_prepared"
            if self._prepared
            else "disconnected"
            if self.connection is None
            else "already_transitioning"
            if output.mixer.transitioning
            else None
        )
        if reason is not None:
            _LOGGER.info(
                "engine.audio.prepare_rejected preparation=%s outgoing=%s reason=%s",
                preparation_id,
                outgoing_attempt_id,
                reason,
            )
            return False
        output = cast(_Output, output)
        duration = output.attempt.source.track.metadata.duration_seconds
        if duration is None or not isfinite(seconds) or seconds < 0:
            _LOGGER.info(
                "engine.audio.prepare_rejected preparation=%s outgoing=%s "
                "reason=invalid_timing duration=%s seconds=%s",
                preparation_id,
                outgoing_attempt_id,
                duration,
                seconds,
            )
            return False
        audio = await self._create_buffer(source)
        prepared = _Prepared(
            preparation_id,
            output,
            source,
            audio,
            crossfade_seconds=seconds,
        )
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
                if prepared.staged:
                    _LOGGER.info(
                        "engine.audio.prepared preparation=%s outgoing=%s audio_pid=%s "
                        "position=%.3f duration=%.3f lead_seconds=%.3f",
                        preparation_id,
                        outgoing_attempt_id,
                        getattr(audio, "process_id", None),
                        output.mixer.position_seconds,
                        duration,
                        max(0.0, duration - output.mixer.position_seconds - seconds),
                    )
            if not prepared.staged:
                _LOGGER.warning(
                    "engine.audio.prepare_not_staged preparation=%s outgoing=%s "
                    "ready=%s output_current=%s finishing=%s",
                    preparation_id,
                    outgoing_attempt_id,
                    ready,
                    self._output is output,
                    output.finishing,
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
            _LOGGER.debug(
                "engine.audio.discard_ignored preparation=%s current_preparation=%s",
                preparation_id,
                prepared.id if prepared else None,
            )
            return
        self._prepared = None
        if prepared.staged:
            discarded = prepared.output.mixer.discard()
            if discarded is None:
                _LOGGER.debug(
                    "engine.audio.discard_transferred preparation=%s", preparation_id
                )
                return  # Activation/retirement now owns this buffer.
        cleanup = asyncio.create_task(asyncio.to_thread(prepared.buffer.cleanup))
        try:
            await asyncio.shield(cleanup)
        finally:
            await cleanup
        _LOGGER.info("engine.audio.discarded preparation=%s", preparation_id)

    def start_transition(
        self,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
    ) -> bool:
        output, prepared = self._output, self._prepared
        reason = (
            "no_output"
            if output is None
            else "no_preparation"
            if prepared is None
            else "preparation_mismatch"
            if prepared.id != preparation_id
            else "output_changed"
            if prepared.output is not output
            else "not_staged"
            if not prepared.staged
            else None
        )
        if reason is not None:
            _LOGGER.warning(
                "engine.audio.transition_rejected attempt=%s outgoing=%s "
                "preparation=%s reason=%s",
                attempt_id,
                outgoing_attempt_id,
                preparation_id,
                reason,
            )
            return False
        output = cast(_Output, output)
        prepared = cast(_Prepared, prepared)
        with output.lock:
            reason = (
                "attempt_mismatch"
                if output.attempt.id != outgoing_attempt_id
                else "finishing"
                if output.finishing
                else "activating"
                if output.activating
                else None
            )
            if reason is not None:
                _LOGGER.warning(
                    "engine.audio.transition_rejected attempt=%s outgoing=%s "
                    "preparation=%s reason=%s",
                    attempt_id,
                    outgoing_attempt_id,
                    preparation_id,
                    reason,
                )
                return False
            output.activating = True
            output.activation_done.clear()
            outgoing_attempt = output.attempt
        retiring = _RetiringAttempt(
            outgoing_attempt,
            output.mixer.position_seconds,
            self._diagnostics(output.mixer, outgoing_attempt),
        )
        actor_id, actor_name = current_actor()
        attempt = _Attempt(
            id=attempt_id,
            source=prepared.source,
            notify=notify,
            trace_id=current_trace_id() or request_trace_id(),
            actor_id=actor_id,
            actor_name=actor_name,
            created_at=time.monotonic(),
            start_mode=("crossfade" if prepared.crossfade_seconds > 0 else "preloaded"),
            audio_pid=getattr(prepared.buffer, "process_id", None),
            first_frame_seconds=getattr(prepared.buffer, "first_frame_seconds", None),
            preparation_id=preparation_id,
        )
        with output.lock:
            output.retiring = retiring

        def activated() -> None:
            with output.lock:
                output.attempt = attempt

        def faded() -> None:
            with output.lock:
                completed = output.retiring
                if completed is retiring:
                    output.retiring = None
            if completed is retiring:
                self._log_attempt_summary(
                    retiring.attempt,
                    position=(
                        retiring.attempt.source.track.metadata.duration_seconds
                        or retiring.position
                    ),
                    reason="crossfade",
                    diagnostics=retiring.diagnostics,
                )
            with log_context(
                attempt.actor_id,
                attempt.actor_name,
                trace_id=attempt.trace_id,
            ):
                notify(CrossfadeCompleted(attempt_id, preparation_id))

        try:
            accepted = output.mixer.activate(
                faded,
                activated,
                lambda: self._started(output, attempt),
            )
            if accepted:
                _LOGGER.info(
                    "engine.audio.transition preparation=%s prepared_age_seconds=%.3f",
                    preparation_id,
                    time.monotonic() - prepared.created_at,
                )
                self._prepared = None
            else:
                with output.lock:
                    if output.retiring is retiring:
                        output.retiring = None
                self._log_attempt_summary(
                    attempt,
                    position=0,
                    reason="transition_rejected",
                    diagnostics=AudioDiagnostics(
                        first_frame_seconds=attempt.first_frame_seconds
                    ),
                )
                _LOGGER.warning(
                    "engine.audio.transition_rejected attempt=%s outgoing=%s "
                    "preparation=%s reason=mixer_rejected",
                    attempt_id,
                    outgoing_attempt_id,
                    preparation_id,
                )
            return accepted
        except BaseException as error:
            with output.lock:
                if output.retiring is retiring:
                    output.retiring = None
            self._log_attempt_summary(
                attempt,
                position=0,
                reason="transition_failed",
                diagnostics=AudioDiagnostics(
                    first_frame_seconds=attempt.first_frame_seconds
                ),
                error=error,
            )
            raise
        finally:
            with output.lock:
                output.activating = False
                output.activation_done.set()

    async def close(self) -> None:
        started_at = time.monotonic()
        _LOGGER.info(
            "engine.audio.closing connection=%s attempt=%s preparation=%s",
            self._connection.connection_id if self._connection else None,
            self._output.attempt.id if self._output else None,
            self._prepared.id if self._prepared else None,
        )
        self._closing = True
        async with self._voice_lock:
            await self._disconnect(lost=False)
        if progress := self.progress:
            await self.stop(progress.attempt_id)
        if self._prepared:
            await self.discard_next(self._prepared.id)
        _LOGGER.info("engine.audio.closed elapsed=%.3f", time.monotonic() - started_at)
