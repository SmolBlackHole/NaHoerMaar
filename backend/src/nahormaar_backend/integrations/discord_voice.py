# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord voice connection and bounded FFmpeg audio output."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import discord
from discord.opus import OpusNotLoaded

from ..application.audio import ResolvedTrack, TrackError, VoiceChannelInfo, VoiceError
from .audio_sources import FFmpegSource, MediaStreamError, VolumeSource
from .audio_mixer import BufferedAudio, CrossfadeSource, FRAME_SECONDS
from .daily_bio import update_daily_bio
from .discord_commands import DiscordCommands

_VOICE_MONITOR_SECONDS = 0.25
_PRESENCE_INTERVAL_SECONDS = 5.0
_PRESENCE_TEXT_LIMIT = 128
_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Playback:
    source: CrossfadeSource
    track: ResolvedTrack
    after: Callable[[Exception | None], None]
    cancellation_error: Exception | None = None


class _DiscordClient(discord.Client):
    def __init__(self, owner: DiscordVoice, *, intents: discord.Intents) -> None:
        super().__init__(intents=intents)
        self._owner = owner
        self.commands: DiscordCommands | None = None

    async def setup_hook(self) -> None:
        if self.commands is not None:
            await self.commands.register()

    async def on_ready(self) -> None:
        self._owner._discord_ready()  # pyright: ignore[reportPrivateUsage]

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if (
            self.user is not None
            and member.id == self.user.id
            and after.channel is None
            and before.channel is not None
        ):
            previous_channel_id = before.channel.id
            if not self._owner._consume_expected_disconnect(  # pyright: ignore[reportPrivateUsage]
                previous_channel_id
            ):
                await self._owner._discord_voice_disconnected(previous_channel_id)  # pyright: ignore[reportPrivateUsage]

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        voice = self._owner._voice  # pyright: ignore[reportPrivateUsage]
        if voice is not None and voice.guild.id == guild.id:
            await self._owner._discord_voice_disconnected(voice.channel.id)  # pyright: ignore[reportPrivateUsage]


class DiscordVoice:
    """One active voice connection across the bot's joined Discord servers."""

    def __init__(self, ffmpeg_path: Path, volume: float = 1.0) -> None:
        self._validate_volume(volume)
        self._ffmpeg_path = ffmpeg_path
        self._volume = volume
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        self._client = _DiscordClient(self, intents=intents)
        self._ready = asyncio.Event()
        self._startup_error: VoiceError | None = None
        self._started = False
        self._closing = False
        self._voice: discord.VoiceClient | None = None
        self._playback: _Playback | None = None
        self._preparing: BufferedAudio | None = None
        self._disconnect_handler: Callable[[], None] = lambda: None
        self._connection_generation = 0
        self._expected_disconnects: dict[int, int] = {}
        self._monitor: asyncio.Task[None] | None = None
        self._connection_lock = asyncio.Lock()
        self._activity: discord.BaseActivity = discord.CustomActivity(
            name="Bereit für Musik"
        )
        self._sent_activity: discord.BaseActivity | None = None
        self._presence_task: asyncio.Task[None] | None = None
        self._bio_task: asyncio.Task[None] | None = None

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

    async def start(self, token: str) -> None:
        """Run the Discord client until close() is called."""
        if self._started:
            raise VoiceError("Discord client has already been started.")
        self._started = True
        try:
            self._validate_voice_dependencies()
            self._presence_task = asyncio.create_task(
                self._update_presence(), name="discord-presence"
            )
            self._bio_task = asyncio.create_task(
                self._update_bio(), name="discord-daily-bio"
            )
            await self._client.start(token)
        except asyncio.CancelledError:
            await self.close()
            raise
        except discord.LoginFailure as error:
            self._startup_error = VoiceError("Discord authentication failed.")
            self._ready.set()
            await self._client.close()
            raise self._startup_error from error
        except VoiceError as error:
            self._startup_error = error
            self._ready.set()
            await self._client.close()
            raise
        except Exception as error:
            self._startup_error = VoiceError("Discord client failed to start.")
            self._ready.set()
            await self._client.close()
            raise self._startup_error from error
        finally:
            self._ready.set()
            await self._stop_profile_tasks()

    @staticmethod
    def _validate_voice_dependencies() -> None:
        if importlib.util.find_spec("davey") is None:
            raise VoiceError("Discord DAVE voice support is unavailable.")
        try:
            discord.opus.Encoder()
        except (OpusNotLoaded, OSError) as error:
            raise VoiceError("Discord Opus encoding support is unavailable.") from error

    async def wait_until_ready(self) -> None:
        await self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error

    def _discord_ready(self) -> None:
        self._sent_activity = None
        self._ready.set()

    def _set_activity(
        self, track: ResolvedTrack | None = None, *, paused: bool = False
    ) -> None:
        if track is None:
            self._activity = discord.CustomActivity(name="Bereit für Musik")
            return
        title = " ".join((track.title or "YouTube").split()) or "YouTube"
        uploader = " ".join((track.uploader or "").split())
        if paused:
            self._activity = discord.CustomActivity(
                name=f"Pausiert: {title}"[:_PRESENCE_TEXT_LIMIT]
            )
        else:
            self._activity = discord.Activity(
                type=discord.ActivityType.listening,
                name=title[:_PRESENCE_TEXT_LIMIT],
                state=uploader[:_PRESENCE_TEXT_LIMIT] or None,
            )

    async def _update_presence(self) -> None:
        await self._ready.wait()
        while True:
            activity = self._activity
            if self._client.is_ready() and (
                self._sent_activity is None
                or activity.to_dict() != self._sent_activity.to_dict()
            ):
                try:
                    async with asyncio.timeout(10):
                        await self._client.change_presence(activity=activity)
                except Exception as error:
                    _LOGGER.warning(
                        "Discord presence update failed (%s); will retry.",
                        type(error).__name__,
                    )
                else:
                    self._sent_activity = activity
            await asyncio.sleep(_PRESENCE_INTERVAL_SECONDS)

    async def _update_bio(self) -> None:
        await self._ready.wait()
        await update_daily_bio(self._client)

    async def _stop_profile_tasks(self) -> None:
        tasks = tuple(
            task for task in (self._presence_task, self._bio_task) if task is not None
        )
        self._presence_task = self._bio_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        await self._stop_profile_tasks()
        try:
            await self.disconnect()
        finally:
            await self._client.close()

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

    def set_disconnect_handler(self, handler: Callable[[], None]) -> None:
        self._disconnect_handler = handler

    def install_commands(
        self, access_path: Path, connect: Callable[[int], Awaitable[object]]
    ) -> None:
        self._client.commands = DiscordCommands(self._client, access_path, connect)

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
                await self._stop_playback(
                    VoiceError("Discord voice connection was closed.")
                )
                return
            await self._disconnect_voice(voice, report_loss=False)

    async def _disconnect_voice(
        self, voice: discord.VoiceClient, *, report_loss: bool
    ) -> None:
        if voice is not self._voice:
            return
        await self._cancel_monitor()
        self._connection_generation += 1
        cleanup_error: VoiceError | None = None
        try:
            await self._stop_playback(
                VoiceError("Discord voice connection was lost."), voice=voice
            )
        except VoiceError as error:
            cleanup_error = error
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
        if report_loss:
            self._disconnect_handler()
        if cleanup_error is not None:
            raise cleanup_error

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
        after: Callable[[Exception | None], None],
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
            )
        except FileNotFoundError as error:
            raise VoiceError(
                "The configured FFmpeg executable is unavailable."
            ) from error
        except OSError as error:
            raise VoiceError("FFmpeg could not be started.") from error

        try:
            source = CrossfadeSource(
                BufferedAudio(VolumeSource(pcm)),
                volume=self._volume,
                position=position_seconds,
                paused=paused,
            )
        except Exception as error:
            pcm.cleanup()
            raise VoiceError("Discord audio processing could not start.") from error
        playback = _Playback(source=source, track=track, after=after)
        self._playback = playback
        _LOGGER.info(
            "voice.audio_started channel=%s audio_pid=%s duration=%s paused=%s",
            self.channel_id,
            pcm.process_id,
            track.duration_seconds,
            paused,
        )

        def completed(error: Exception | None) -> None:
            _LOGGER.info(
                "voice.audio_completed channel=%s position=%.3f error=%s cancelled=%s",
                self.channel_id,
                source.position_seconds,
                type(error).__name__ if error is not None else "none",
                playback.cancellation_error is not None,
            )
            try:
                source.cleanup()
            except Exception:
                playback.after(VoiceError("FFmpeg process cleanup failed."))
                return
            reported = playback.cancellation_error
            if reported is None and isinstance(error, MediaStreamError):
                reported = TrackError("The audio stream failed.", retryable=True)
            elif reported is None and error is not None:
                reported = VoiceError("Discord audio playback failed.")
            playback.after(reported)

        try:
            voice.play(source, after=completed)
            if paused:
                voice.pause()
        except Exception as error:
            source.cleanup()
            self._playback = None
            raise VoiceError("Discord audio playback could not start.") from error
        self._set_activity(track, paused=paused)

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
            or playback.track.duration_seconds is None
            or not self.connected
            or self.transitioning
        ):
            return False
        raw = FFmpegSource(
            self._ffmpeg_path, track.stream_url, track.headers, opus=track.is_opus
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
                    duration=playback.track.duration_seconds,
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
        after: Callable[[Exception | None], None],
        on_faded: Callable[[], None],
    ) -> bool:
        playback = self._playback
        if playback is None:
            return False

        def activated() -> None:
            playback.track = track
            playback.after = after

        if not playback.source.activate(on_faded, activated):
            return False
        self._set_activity(track)
        return True

    async def stop(self) -> None:
        await self._stop_playback(None)

    async def _stop_playback(
        self,
        error: Exception | None,
        *,
        voice: discord.VoiceClient | None = None,
    ) -> None:
        self._set_activity()
        playback = self._playback
        if playback is None:
            await self.discard_next()
            return
        playback.cancellation_error = error
        _LOGGER.info(
            "voice.audio_stop channel=%s position=%.3f error=%s",
            self.channel_id,
            playback.source.position_seconds,
            type(error).__name__ if error is not None else "none",
        )
        output = self._voice if voice is None else voice
        if output is not None:
            output.stop()
        try:
            await asyncio.to_thread(playback.source.cleanup)
            await self.discard_next()
        except Exception as cleanup_error:
            raise VoiceError("FFmpeg process cleanup failed.") from cleanup_error
        if self._playback is playback:
            self._playback = None

    def pause(self) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        if self._playback is not None:
            self._playback.source.pause()
        voice.pause()
        if self._playback is not None:
            self._set_activity(self._playback.track, paused=True)

    def resume(self) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        if self._playback is not None:
            self._playback.source.resume()
        voice.resume()
        if self._playback is not None:
            self._set_activity(self._playback.track)

    def set_volume(self, volume: float) -> None:
        self._validate_volume(volume)
        self._volume = volume
        if self._playback is not None:
            self._playback.source.volume = volume
