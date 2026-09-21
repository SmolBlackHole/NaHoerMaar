# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord voice connection and bounded FFmpeg audio output."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import discord
from discord.opus import OpusNotLoaded

from ..application.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioStarted,
    ResolvedTrack,
    TrackError,
    VoiceChannelInfo,
    VoiceDisconnected,
    VoiceError,
)
from .audio_sources import FFmpegSource, MediaStreamError, VolumeSource
from .audio_mixer import BufferedAudio, CrossfadeSource, FRAME_SECONDS

_VOICE_MONITOR_SECONDS = 0.25
_NATURAL_EOF_TOLERANCE_SECONDS = 1.0
_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Attempt:
    track: ResolvedTrack
    attempt_id: UUID
    notify: Callable[[AudioEvent], None]
    started: bool = False


@dataclass(slots=True)
class _Playback:
    source: CrossfadeSource
    current: _Attempt
    paused: bool
    lock: threading.RLock = field(default_factory=threading.RLock)
    forced_reason: AudioEndReason | None = None
    forced_error: Exception | None = None
    activating: bool = False
    activation_finished: threading.Event = field(default_factory=threading.Event)
    finishing: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    cleanup_error: VoiceError | None = None

    def __post_init__(self) -> None:
        self.activation_finished.set()


class PlaybackActivity(Protocol):
    def __call__(
        self, track: ResolvedTrack | None = None, *, paused: bool = False
    ) -> None: ...


class DiscordVoice:
    """One active voice connection across the bot's joined Discord servers."""

    def __init__(
        self,
        ffmpeg_path: Path,
        volume: float = 1.0,
        *,
        client: discord.Client,
        activity: PlaybackActivity,
    ) -> None:
        self._validate_volume(volume)
        self._ffmpeg_path = ffmpeg_path
        self._volume = volume
        self._client = client
        self._set_activity = activity
        self._closing = False
        self._voice: discord.VoiceClient | None = None
        self._playback: _Playback | None = None
        self._preparing: BufferedAudio | None = None
        self._disconnect_handler: Callable[[VoiceDisconnected], None] = lambda _: None
        self._connection_generation = 0
        self._expected_disconnects: dict[int, int] = {}
        self._monitor: asyncio.Task[None] | None = None
        self._connection_lock = asyncio.Lock()

    @staticmethod
    def _validate_volume(volume: float) -> None:
        if not 0.0 <= volume <= 1.0:
            raise ValueError("Volume must be between 0 and 1.")

    @property
    def connected(self) -> bool:
        return self._voice is not None and self._voice.is_connected()

    @property
    def channel_id(self) -> int | None:
        if not self.connected or self._voice is None:
            return None
        return self._voice.channel.id

    @staticmethod
    def validate_dependencies() -> None:
        if importlib.util.find_spec("davey") is None:
            raise VoiceError("Discord DAVE voice support is unavailable.")
        try:
            discord.opus.Encoder()
        except (OpusNotLoaded, OSError) as error:
            raise VoiceError("Discord Opus encoding support is unavailable.") from error

    async def close(self) -> None:
        """Close Session-owned output; the gateway retains client ownership."""
        if self._closing:
            return
        self._closing = True
        await self.disconnect()

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if (
            self._client.user is not None
            and member.id == self._client.user.id
            and after.channel is None
            and before.channel is not None
        ):
            previous_channel_id = before.channel.id
            if not self._consume_expected_disconnect(previous_channel_id):
                await self._discord_voice_disconnected(previous_channel_id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        voice = self._voice
        if voice is not None and voice.guild.id == guild.id:
            await self._discord_voice_disconnected(voice.channel.id)

    def channels(self) -> tuple[VoiceChannelInfo, ...]:
        return tuple(
            VoiceChannelInfo(
                id=channel.id,
                name=channel.name,
                can_connect=(
                    channel.permissions_for(guild.me).view_channel
                    and channel.permissions_for(guild.me).connect
                ),
                can_speak=channel.permissions_for(guild.me).speak,
                guild_id=guild.id,
                guild_name=guild.name,
            )
            for guild in sorted(
                self._client.guilds, key=lambda guild: (guild.name.casefold(), guild.id)
            )
            if not guild.unavailable
            for channel in guild.voice_channels
        )

    def set_disconnect_handler(
        self, handler: Callable[[VoiceDisconnected], None]
    ) -> None:
        self._disconnect_handler = handler

    async def connect(self, channel_id: int) -> None:
        async with self._connection_lock:
            channel = self._client.get_channel(channel_id)
            if (
                not isinstance(channel, discord.VoiceChannel)
                or channel.guild.unavailable
            ):
                raise VoiceError(
                    "The selected voice channel is unavailable. Refresh the channel list."
                )
            permissions = channel.permissions_for(channel.guild.me)
            if not permissions.view_channel or not permissions.connect:
                raise VoiceError(
                    "The bot cannot connect to the selected voice channel."
                )
            if not permissions.speak:
                raise VoiceError("The bot cannot speak in the selected voice channel.")

            current = self._voice
            try:
                if current is not None and current.is_connected():
                    if current.channel.id == channel_id:
                        return
                    if current.guild.id == channel.guild.id:
                        await self._cancel_monitor()
                        await current.move_to(channel)
                        self._start_monitor(current)
                        return

                if current is not None:
                    await self._disconnect_voice(current, report_loss=False)
                connected: discord.VoiceClient = await channel.connect(
                    reconnect=False,
                    timeout=30.0,
                    self_deaf=True,
                )
            except discord.Forbidden as error:
                raise VoiceError(
                    "Discord denied access to the selected voice channel."
                ) from error
            except (
                TimeoutError,
                discord.HTTPException,
                discord.ClientException,
            ) as error:
                raise VoiceError("Discord voice connection failed.") from error

            self._voice = connected
            self._connection_generation += 1
            self._start_monitor(self._voice)

    async def disconnect(self) -> None:
        async with self._connection_lock:
            voice = self._voice
            if voice is None:
                await self._stop_playback(AudioEndReason.STOPPED)
                return
            await self._disconnect_voice(voice, report_loss=False)

    async def _disconnect_voice(
        self, voice: discord.VoiceClient, *, report_loss: bool
    ) -> None:
        if voice is not self._voice:
            return
        disconnected = self._disconnect_event(voice) if report_loss else None
        await self._cancel_monitor()
        self._connection_generation += 1
        if report_loss:
            # Completion can reach the Session while cleanup awaits a worker
            # thread. Expose the lost connection before that callback so the
            # Session suspends instead of retrying during teardown.
            self._voice = None
        cleanup_error: VoiceError | None = None
        try:
            await self._stop_playback(
                AudioEndReason.INTERRUPTED if report_loss else AudioEndReason.STOPPED,
                voice=voice,
            )
        except VoiceError as error:
            cleanup_error = error
        if not report_loss:
            self._voice = None
        channel_id = voice.channel.id
        if not report_loss:
            self._expected_disconnects[channel_id] = (
                self._expected_disconnects.get(channel_id, 0) + 1
            )
        try:
            await voice.disconnect(force=True)
        except (discord.HTTPException, discord.ClientException) as error:
            if not report_loss:
                raise VoiceError("Discord voice disconnect failed.") from error
        if disconnected is not None:
            self._disconnect_handler(disconnected)
        if cleanup_error is not None:
            raise cleanup_error

    def _disconnect_event(self, voice: discord.VoiceClient) -> VoiceDisconnected:
        playback = self._playback
        if playback is None:
            return VoiceDisconnected(None, voice.channel.id, 0.0, False)
        while True:
            with playback.lock:
                attempt = playback.current
                paused = playback.paused
            position = playback.source.position_seconds
            with playback.lock:
                if playback.current is attempt and playback.paused is paused:
                    return VoiceDisconnected(
                        attempt.attempt_id,
                        voice.channel.id,
                        position,
                        paused,
                    )

    def _consume_expected_disconnect(self, channel_id: int | None) -> bool:
        if channel_id is None:
            return False
        count = self._expected_disconnects.get(channel_id, 0)
        if count == 0:
            return False
        if count == 1:
            del self._expected_disconnects[channel_id]
        else:
            self._expected_disconnects[channel_id] = count - 1
        return True

    async def _discord_voice_disconnected(self, channel_id: int | None = None) -> None:
        async with self._connection_lock:
            voice = self._voice
            if voice is not None and (
                channel_id is None or voice.channel.id == channel_id
            ):
                await self._disconnect_voice(voice, report_loss=True)

    def _start_monitor(self, voice: discord.VoiceClient) -> None:
        generation = self._connection_generation
        self._monitor = asyncio.create_task(
            self._monitor_connection(voice, generation),
            name="discord-voice-monitor",
        )

    async def _cancel_monitor(self) -> None:
        monitor = self._monitor
        self._monitor = None
        if monitor is None or monitor is asyncio.current_task():
            return
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass

    async def _monitor_connection(
        self, voice: discord.VoiceClient, generation: int
    ) -> None:
        while True:
            await asyncio.sleep(_VOICE_MONITOR_SECONDS)
            if voice is not self._voice or generation != self._connection_generation:
                return
            if not voice.is_connected():
                await self._discord_voice_disconnected()
                return

    def play(
        self,
        track: ResolvedTrack,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        if not track.stream_url:
            raise TrackError("The audio stream URL is empty.")
        if self._playback is not None and not self._playback.source.closed:
            raise VoiceError("Previous audio cleanup is still in progress.")

        try:
            pcm = FFmpegSource(
                self._ffmpeg_path,
                track.stream_url,
                track.headers,
                opus=track.is_opus,
                position_seconds=position_seconds,
                duration_seconds=track.duration_seconds,
            )
        except FileNotFoundError as error:
            raise VoiceError(
                "The configured FFmpeg executable is unavailable."
            ) from error
        except OSError as error:
            raise VoiceError("FFmpeg could not be started.") from error

        try:
            attempt = _Attempt(track, attempt_id, notify)
            playback: _Playback
            source = CrossfadeSource(
                BufferedAudio(VolumeSource(pcm)),
                volume=self._volume,
                position=position_seconds,
                paused=paused,
                on_started=lambda: self._started(playback, attempt),
            )
        except Exception as error:
            pcm.cleanup()
            raise VoiceError("Discord audio processing could not start.") from error
        playback = _Playback(source=source, current=attempt, paused=paused)
        self._playback = playback
        _LOGGER.info(
            "voice.audio_started channel=%s audio_pid=%s duration=%s paused=%s",
            self.channel_id,
            pcm.process_id,
            track.duration_seconds,
            paused,
        )

        def completed(error: Exception | None) -> None:
            self._complete_playback(playback, error)

        try:
            voice.play(source, after=completed)
            if paused:
                voice.pause()
        except Exception as error:
            source.cleanup()
            self._playback = None
            raise VoiceError("Discord audio playback could not start.") from error
        self._set_activity(track, paused=paused)

    def _started(self, playback: _Playback, attempt: _Attempt) -> None:
        position = playback.source.position_seconds
        with playback.lock:
            if (
                playback is not self._playback
                or playback.current is not attempt
                or attempt.started
                or playback.finishing
            ):
                return
            attempt.started = True
            attempt.notify(AudioStarted(attempt.attempt_id, position))

    def _complete_playback(
        self, playback: _Playback, player_error: Exception | None
    ) -> None:
        while True:
            with playback.lock:
                if playback.finishing:
                    duplicate = True
                    activation = None
                    attempt = None
                elif playback.activating:
                    duplicate = False
                    activation = playback.activation_finished
                    attempt = None
                else:
                    duplicate = False
                    activation = None
                    attempt = playback.current
            if duplicate:
                playback.finished.wait()
                return
            if activation is not None:
                activation.wait()
                continue

            candidate = cast(_Attempt, attempt)
            position = playback.source.position_seconds
            terminal_error = playback.source.current_error
            with playback.lock:
                if playback.finishing:
                    duplicate = True
                elif playback.activating or playback.current is not candidate:
                    duplicate = False
                else:
                    playback.finishing = True
                    active_attempt = candidate
                    forced_reason = playback.forced_reason
                    forced_error = playback.forced_error
                    break
            if duplicate:
                playback.finished.wait()
                return

        cleanup_error: VoiceError | None = None
        try:
            playback.source.cleanup()
        except Exception:
            cleanup_error = VoiceError("FFmpeg process cleanup failed.")

        if forced_reason is not None:
            reason = forced_reason
            error = forced_error
        elif isinstance(terminal_error, MediaStreamError) or isinstance(
            player_error, MediaStreamError
        ):
            reason = AudioEndReason.INTERRUPTED
            error = TrackError("The audio stream failed.", retryable=True)
        elif terminal_error is not None or player_error is not None:
            reason = AudioEndReason.OUTPUT_FAILED
            error = VoiceError("Discord audio playback failed.")
        elif (
            active_attempt.track.duration_seconds is not None
            and position + _NATURAL_EOF_TOLERANCE_SECONDS
            < active_attempt.track.duration_seconds
        ):
            reason = AudioEndReason.INTERRUPTED
            error = TrackError("The audio stream ended early.", retryable=True)
        else:
            reason = AudioEndReason.NATURAL
            error = None
        if cleanup_error is not None:
            playback.cleanup_error = cleanup_error
            if reason is not AudioEndReason.STOPPED:
                reason = AudioEndReason.OUTPUT_FAILED
            error = cleanup_error

        _LOGGER.info(
            "voice.audio_completed channel=%s position=%.3f reason=%s error=%s",
            self.channel_id,
            position,
            reason,
            type(error).__name__ if error is not None else "none",
        )
        if self._playback is playback:
            self._playback = None
        try:
            active_attempt.notify(
                AudioCompleted(active_attempt.attempt_id, reason, position, error)
            )
        finally:
            playback.finished.set()

    @property
    def transitioning(self) -> bool:
        return self._playback is not None and self._playback.source.transitioning

    @property
    def position_seconds(self) -> float | None:
        playback = self._playback
        if playback is None or playback.source.closed:
            return None
        return playback.source.position_seconds

    async def prepare_next(
        self, track: ResolvedTrack, seconds: float, on_due: Callable[[], None]
    ) -> bool:
        playback = self._playback
        if self._preparing is not None:
            raise VoiceError("Previous prepared audio cleanup is incomplete.")
        if (
            playback is None
            or playback.current.track.duration_seconds is None
            or not self.connected
            or self.transitioning
        ):
            return False
        raw = FFmpegSource(
            self._ffmpeg_path,
            track.stream_url,
            track.headers,
            opus=track.is_opus,
            duration_seconds=track.duration_seconds,
        )
        try:
            audio = BufferedAudio(VolumeSource(raw))
        except BaseException:
            await asyncio.to_thread(raw.cleanup)
            raise
        self._preparing = audio
        staged = False
        try:
            ready = await asyncio.to_thread(
                audio.wait_ready, max(1, round(seconds / FRAME_SECONDS))
            )
            if ready and self._playback is playback:
                staged = playback.source.stage(
                    audio,
                    seconds=seconds,
                    duration=playback.current.track.duration_seconds,
                    on_due=on_due,
                )
            _LOGGER.info(
                "crossfade.buffer_ready audio_pid=%s ready=%s staged=%s seconds=%.3f",
                raw.process_id,
                ready,
                staged,
                seconds,
            )
            return staged
        finally:
            if staged:
                self._preparing = None
            else:
                try:
                    await asyncio.to_thread(audio.cleanup)
                except Exception as error:
                    raise VoiceError("Prepared audio cleanup failed.") from error
                if self._preparing is audio:
                    self._preparing = None

    async def discard_next(self) -> None:
        preparing = self._preparing
        if preparing is not None:
            await asyncio.to_thread(preparing.cleanup)
            if self._preparing is preparing:
                self._preparing = None
        playback = self._playback
        if playback is not None:
            audio = playback.source.discard()
            if audio is not None:
                self._preparing = audio
                await asyncio.to_thread(audio.cleanup)
                if self._preparing is audio:
                    self._preparing = None

    def start_transition(
        self,
        track: ResolvedTrack,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        on_faded: Callable[[], None],
    ) -> bool:
        playback = self._playback
        if playback is None:
            return False

        attempt = _Attempt(track, attempt_id, notify)

        with playback.lock:
            if (
                playback is not self._playback
                or playback.finishing
                or playback.activating
            ):
                return False
            playback.activating = True
            playback.activation_finished.clear()

        def activated() -> None:
            with playback.lock:
                if (
                    playback is self._playback
                    and playback.activating
                    and not playback.finishing
                ):
                    playback.current = attempt
                    playback.paused = False

        try:
            started = playback.source.activate(
                on_faded,
                activated,
                lambda: self._started(playback, attempt),
            )
        finally:
            with playback.lock:
                playback.activating = False
                playback.activation_finished.set()
        if not started:
            return False
        self._set_activity(track)
        return True

    async def stop(self) -> None:
        await self._stop_playback(AudioEndReason.STOPPED)

    async def _stop_playback(
        self,
        reason: AudioEndReason,
        *,
        voice: discord.VoiceClient | None = None,
    ) -> None:
        self._set_activity()
        playback = self._playback
        if playback is None:
            await self.discard_next()
            return
        with playback.lock:
            playback.forced_reason = reason
        _LOGGER.info(
            "voice.audio_stop channel=%s position=%.3f error=%s",
            self.channel_id,
            playback.source.position_seconds,
            reason,
        )
        output = self._voice if voice is None else voice
        if output is not None:
            output.stop()
        try:
            await asyncio.to_thread(self._complete_playback, playback, None)
            await self.discard_next()
        except Exception as cleanup_error:
            raise VoiceError("FFmpeg process cleanup failed.") from cleanup_error
        if playback.cleanup_error is not None:
            raise playback.cleanup_error

    def pause(self) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        if self._playback is not None:
            self._playback.source.pause()
            self._playback.paused = True
        voice.pause()
        if self._playback is not None:
            self._set_activity(self._playback.current.track, paused=True)

    def resume(self) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        if self._playback is not None:
            self._playback.source.resume()
            self._playback.paused = False
        voice.resume()
        if self._playback is not None:
            self._set_activity(self._playback.current.track)

    def set_volume(self, volume: float) -> None:
        self._validate_volume(volume)
        self._volume = volume
        if self._playback is not None:
            self._playback.source.volume = volume
