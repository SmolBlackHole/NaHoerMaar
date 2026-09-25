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
import random
import threading
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from math import isfinite
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

import discord
from discord import app_commands

from nahoermaar.listening.service import VoiceMemberState
from nahoermaar.observability import (
    LogContext,
    current_actor_id,
    current_correlation_id,
    log_context,
)
from nahoermaar.player.playback import (
    AudioCompleted,
    AudioEndReason,
    AudioError,
    AudioEvent,
    AudioProgress,
    AudioSourceNotReady,
    AudioStarted,
    CrossfadeCompleted,
    CrossfadeDue,
    NowPlaying,
    PlayableSource,
    VoiceChannel,
    VoiceConnection,
    VoiceDisconnected,
    VoiceError,
)
from nahoermaar.users.domain import DiscordMember

from .audio import (
    FRAME_SECONDS,
    AudioDiagnostics,
    BufferedAudio,
    CrossfadeSource,
    FFmpegSource,
    MediaStreamError,
    VolumeSource,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Attempt:
    id: UUID
    source: PlayableSource
    notify: Callable[[AudioEvent], None]
    correlation_id: UUID
    actor_id: UUID | None
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
        self._audience_handler: Callable[[], None] = lambda: None
        self._sent_activity: dict[str, object] | None = None
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

    def set_audience_handler(self, handler: Callable[[], None]) -> None:
        self._audience_handler = handler

    def audience(self) -> tuple[VoiceMemberState, ...]:
        voice = self._voice
        if voice is None or not isinstance(voice.channel, discord.VoiceChannel):
            return ()
        return tuple(
            VoiceMemberState(
                str(member.id),
                member.bot,
                bool(member.voice and (member.voice.deaf or member.voice.self_deaf)),
            )
            for member in voice.channel.members
        )

    async def set_presence(self, presence: NowPlaying) -> None:
        if presence.title is None:
            activity: discord.BaseActivity = discord.CustomActivity(
                name="Bereit für Musik"
            )
        elif presence.paused:
            activity = discord.CustomActivity(name=f"Pausiert: {presence.title}"[:128])
        else:
            activity = discord.Activity(
                type=discord.ActivityType.listening,
                name=presence.title[:128],
                state=presence.artist[:128] if presence.artist else None,
            )
        value = cast(dict[str, object], activity.to_dict())
        if not self._client.is_ready() or value == self._sent_activity:
            return
        async with asyncio.timeout(10):
            await self._client.change_presence(activity=activity)
        self._sent_activity = value

    def reset_presence(self) -> None:
        self._sent_activity = None

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
                    _LOGGER.debug("discord.voice.cancelled_join_failed")
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
                "discord.voice.connected connection=%s channel_id=%s",
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
                        "discord.voice.disconnected connection=%s lost=%s position=%s",
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
        connection = self.connection
        if connection is not None and connection.channel_id in {
            before.channel.id if before.channel is not None else None,
            after.channel.id if after.channel is not None else None,
        }:
            self._audience_handler()

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
            duration_seconds=source.duration_seconds,
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
                _LOGGER.debug("discord.audio.cancelled_buffer_creation_failed")
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
            LogContext(
                correlation_id=attempt.correlation_id,
                actor_id=attempt.actor_id,
            )
        ):
            _LOGGER.info(
                "discord.audio.attempt_summary attempt=%s preparation=%s "
                "provider=%s media_id=%s start_mode=%s audio_pid=%s "
                "first_frame_seconds=%s start_seconds=%s attempt_seconds=%.3f "
                "position=%.3f underruns=%s stalled_seconds=%.3f "
                "max_stall_seconds=%.3f reason=%s error=%s",
                attempt.id,
                attempt.preparation_id,
                attempt.source.provider,
                attempt.source.external_id,
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
                "discord.audio.play_requested attempt=%s provider=%s media_id=%s "
                "position=%.3f paused=%s opus=%s",
                attempt_id,
                source.provider,
                source.external_id,
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
            actor_id = current_actor_id()
            attempt = _Attempt(
                id=attempt_id,
                source=source,
                notify=notify,
                correlation_id=current_correlation_id(),
                actor_id=actor_id,
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
                    "discord.audio.buffer_ready attempt=%s audio_pid=%s "
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
                    "discord.audio.output_submitted attempt=%s elapsed=%.3f",
                    attempt_id,
                    time.monotonic() - started_at,
                )
            except BaseException as error:
                _LOGGER.warning(
                    "discord.audio.play_failed attempt=%s elapsed=%.3f",
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
            LogContext(
                correlation_id=attempt.correlation_id,
                actor_id=attempt.actor_id,
            )
        ):
            _LOGGER.info(
                "discord.audio.started attempt=%s position=%.3f", attempt.id, position
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
                    LogContext(
                        correlation_id=attempt.correlation_id,
                        actor_id=attempt.actor_id,
                    )
                ):
                    _LOGGER.error(
                        "discord.audio.cleanup_failed attempt=%s",
                        attempt.id,
                        exc_info=cleanup_failure,
                    )
            duration = attempt.source.duration_seconds
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
                    LogContext(
                        correlation_id=attempt.correlation_id,
                        actor_id=attempt.actor_id,
                    )
                ):
                    _LOGGER.error(
                        "discord.audio.output_failed attempt=%s",
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
                LogContext(
                    correlation_id=attempt.correlation_id,
                    actor_id=attempt.actor_id,
                )
            ):
                _LOGGER.info(
                    "discord.audio.completed attempt=%s position=%.3f reason=%s",
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
                    "discord.audio.stop_ignored attempt=%s current_attempt=%s",
                    attempt_id,
                    output.attempt.id if output else None,
                )
                return
            _LOGGER.info(
                "discord.audio.stop_requested attempt=%s position=%.3f",
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
            _LOGGER.info("discord.audio.stop_completed attempt=%s", attempt_id)

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
                "discord.audio.paused attempt=%s position=%.3f",
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
                "discord.audio.resumed attempt=%s position=%.3f",
                attempt_id,
                self._output.mixer.position_seconds,
            )

    def set_volume(self, volume: float) -> None:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")
        self._volume = volume
        if self._output:
            self._output.mixer.volume = volume
        _LOGGER.debug("discord.audio.volume_set volume=%.3f", volume)

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
                "discord.audio.prepare_rejected preparation=%s outgoing=%s reason=%s",
                preparation_id,
                outgoing_attempt_id,
                reason,
            )
            return False
        output = cast(_Output, output)
        duration = output.attempt.source.duration_seconds
        if duration is None or not isfinite(seconds) or seconds < 0:
            _LOGGER.info(
                "discord.audio.prepare_rejected preparation=%s outgoing=%s "
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
                            source.duration_seconds,
                        )
                    ),
                )
                if prepared.staged:
                    _LOGGER.info(
                        "discord.audio.prepared preparation=%s outgoing=%s audio_pid=%s "
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
                    "discord.audio.prepare_not_staged preparation=%s outgoing=%s "
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
                "discord.audio.discard_ignored preparation=%s current_preparation=%s",
                preparation_id,
                prepared.id if prepared else None,
            )
            return
        self._prepared = None
        if prepared.staged:
            discarded = prepared.output.mixer.discard()
            if discarded is None:
                _LOGGER.debug(
                    "discord.audio.discard_transferred preparation=%s", preparation_id
                )
                return  # Activation/retirement now owns this buffer.
        cleanup = asyncio.create_task(asyncio.to_thread(prepared.buffer.cleanup))
        try:
            await asyncio.shield(cleanup)
        finally:
            await cleanup
        _LOGGER.info("discord.audio.discarded preparation=%s", preparation_id)

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
                "discord.audio.transition_rejected attempt=%s outgoing=%s "
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
                    "discord.audio.transition_rejected attempt=%s outgoing=%s "
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
        actor_id = current_actor_id()
        attempt = _Attempt(
            id=attempt_id,
            source=prepared.source,
            notify=notify,
            correlation_id=current_correlation_id(),
            actor_id=actor_id,
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
                        retiring.attempt.source.duration_seconds or retiring.position
                    ),
                    reason="crossfade",
                    diagnostics=retiring.diagnostics,
                )
            with log_context(
                LogContext(
                    correlation_id=attempt.correlation_id,
                    actor_id=attempt.actor_id,
                )
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
                    "discord.audio.transition preparation=%s prepared_age_seconds=%.3f",
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
                    "discord.audio.transition_rejected attempt=%s outgoing=%s "
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
            "discord.audio.closing connection=%s attempt=%s preparation=%s",
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
        _LOGGER.info("discord.audio.closed elapsed=%.3f", time.monotonic() - started_at)


_CHECK_INTERVAL_SECONDS = 60.0
_DESCRIPTION_LIMIT = 400


type SummonHandler = Callable[[str, int, UUID], Awaitable[None]]


class DiscordGateway(discord.Client):
    """Own the Discord gateway, slash commands and process-level profile."""

    def __init__(
        self,
        token: str,
        ffmpeg_path: Path,
        quotes_path: Path,
        summon: SummonHandler,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.output = DiscordOutput(self, ffmpeg_path)
        self._token = token
        self._quotes_path = quotes_path
        self._summon_handler = summon
        self._gateway_task: asyncio.Task[None] | None = None
        self._quote_task: asyncio.Task[None] | None = None
        self._commands_synced = False
        self._commands = app_commands.CommandTree(self)
        self._commands.add_command(
            app_commands.Command(
                name="pspsps",
                description="Summon the bot to your voice channel.",
                callback=self._summon,
            )
        )

    @property
    def operational(self) -> bool:
        """Return whether the configured gateway task is connected and alive."""
        return (
            self._gateway_task is not None
            and not self._gateway_task.done()
            and self.is_ready()
            and not self.is_closed()
        )

    def members(self) -> tuple[DiscordMember, ...]:
        """Return human guild members as transport-neutral values."""
        return tuple(
            sorted(
                (
                    DiscordMember(
                        str(member.id),
                        member.name,
                        member.display_name,
                        str(member.display_avatar.url),
                        str(guild.id),
                        guild.name,
                    )
                    for guild in self.guilds
                    for member in guild.members
                    if not member.bot
                ),
                key=lambda member: (
                    member.guild_name.casefold(),
                    member.display_name.casefold(),
                    member.discord_id,
                ),
            )
        )

    async def open(self) -> None:
        if self._gateway_task is not None:
            return
        self.output.validate_dependencies()
        self._gateway_task = asyncio.create_task(
            super().start(self._token),
            name="discord-gateway",
        )
        ready = asyncio.create_task(self.wait_until_ready(), name="discord-ready")
        try:
            async with asyncio.timeout(30):
                done, _pending = await asyncio.wait(
                    (self._gateway_task, ready),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if self._gateway_task in done:
                await self._gateway_task
                raise RuntimeError("Discord gateway stopped before becoming ready.")
            await ready
        except BaseException:
            ready.cancel()
            self._gateway_task.cancel()
            await asyncio.gather(ready, self._gateway_task, return_exceptions=True)
            self._gateway_task = None
            raise
        self._quote_task = asyncio.create_task(
            self._update_daily_bio(),
            name="discord-daily-bio",
        )
        _LOGGER.info("discord.gateway_started guilds=%d", len(self.guilds))

    async def on_ready(self) -> None:
        self.output.reset_presence()
        _LOGGER.info(
            "discord.gateway_ready guilds=%d user_id=%s",
            len(self.guilds),
            self.user.id if self.user else None,
        )
        if self._commands_synced:
            return
        try:
            async with asyncio.timeout(10):
                commands = await self._commands.sync()
        except Exception:
            _LOGGER.exception("discord.commands_registration_failed")
        else:
            self._commands_synced = True
            _LOGGER.info("discord.commands_registered count=%d", len(commands))

    async def on_resumed(self) -> None:
        self.output.reset_presence()
        _LOGGER.info("discord.gateway_resumed guilds=%d", len(self.guilds))

    async def on_disconnect(self) -> None:
        _LOGGER.warning("discord.gateway_disconnected")

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await self.output.on_voice_state_update(member, before, after)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        _LOGGER.info("discord.guild_removed guild_id=%s", guild.id)
        await self.output.on_guild_remove(guild)

    @app_commands.guild_only()
    @app_commands.guild_install()
    async def _summon(self, interaction: discord.Interaction[discord.Client]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = ":3"
        outcome = "joined"
        try:
            member = interaction.user
            if (
                interaction.guild is None
                or not isinstance(member, discord.Member)
                or member.voice is None
                or not isinstance(member.voice.channel, discord.VoiceChannel)
            ):
                message = "Join a voice channel in this server, then call /pspsps."
                outcome = "no_voice_channel"
            else:
                channel = member.voice.channel
                permissions = channel.permissions_for(interaction.guild.me)
                if not (
                    permissions.view_channel
                    and permissions.connect
                    and permissions.speak
                ):
                    message = "I'm missing permissions in your voice channel."
                    outcome = "missing_permissions"
                else:
                    await self._summon_handler(
                        str(member.id),
                        channel.id,
                        uuid5(NAMESPACE_URL, f"discord-interaction:{interaction.id}"),
                    )
        except Exception:
            outcome = "join_failed"
            message = "I couldn't join your voice channel. Please try again."
            _LOGGER.exception(
                "discord.summon_failed discord_id=%s guild_id=%s",
                interaction.user.id,
                interaction.guild_id,
            )
        _LOGGER.info(
            "discord.summon_completed discord_id=%s guild_id=%s outcome=%s",
            interaction.user.id,
            interaction.guild_id,
            outcome,
        )
        await interaction.edit_original_response(
            content=message,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _update_daily_bio(self) -> None:
        updated_day: date | None = None
        while True:
            today = date.today()
            if self.is_ready() and today != updated_day:
                try:
                    quotes = await asyncio.to_thread(load_quotes, self._quotes_path)
                    quote = quote_for_day(quotes, today)
                    async with asyncio.timeout(30):
                        application = await self.application_info()
                        if application.description != quote:
                            await application.edit(description=quote)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception("discord.daily_bio_failed")
                else:
                    updated_day = today
                    _LOGGER.info("discord.daily_bio_updated day=%s", today)
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)

    async def close(self) -> None:
        quote_task, self._quote_task = self._quote_task, None
        if quote_task is not None:
            quote_task.cancel()
            await asyncio.gather(quote_task, return_exceptions=True)
        await self.output.close()
        if not self.is_closed():
            await super().close()
        gateway_task, self._gateway_task = self._gateway_task, None
        if gateway_task is not None and gateway_task is not asyncio.current_task():
            await asyncio.gather(gateway_task, return_exceptions=True)
        _LOGGER.info("discord.gateway_closed")


def load_quotes(path: Path) -> tuple[str, ...]:
    with path.open("rb") as source:
        languages: object = tomllib.load(source).get("quotes")
    if not isinstance(languages, dict) or not languages:
        raise ValueError("quotes must be a non-empty table of language arrays.")
    quotes: list[str] = []
    for language, entries in cast(dict[str, object], languages).items():
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"quotes.{language} must be a non-empty array.")
        for entry in cast(list[object], entries):
            if (
                not isinstance(entry, str)
                or not entry.strip()
                or len(entry.strip()) > _DESCRIPTION_LIMIT
            ):
                raise ValueError(
                    f"quotes.{language} must contain non-empty strings, "
                    "each at most 400 characters."
                )
            quotes.append(entry.strip())
    return tuple(quotes)


def quote_for_day(quotes: tuple[str, ...], day: date) -> str:
    chooser = random.Random(day.isoformat())  # noqa: S311 - display text
    return chooser.choice(quotes)
