# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord voice connection and bounded FFmpeg audio output."""

from __future__ import annotations

import asyncio
import audioop
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import importlib.util
import logging
from pathlib import Path
import re
import struct
import subprocess
import threading
from typing import BinaryIO, cast

import discord
from discord.oggparse import OggError, OggStream
from discord.opus import OpusNotLoaded

from .audio import ResolvedTrack, TrackError, VoiceChannelInfo, VoiceError
from .daily_bio import update_daily_bio


_PCM_FRAME_BYTES = 3_840
_SAMPLES_PER_FRAME = 960
_MUSIC_BITRATE_KBPS = 512
_PROCESS_TIMEOUT_SECONDS = 2.0
_VOICE_MONITOR_SECONDS = 0.25
_PRESENCE_INTERVAL_SECONDS = 5.0
_PRESENCE_TEXT_LIMIT = 128
_LOGGER = logging.getLogger(__name__)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class _MediaStreamError(RuntimeError):
    """Internal marker for a media-specific FFmpeg failure."""


class _FFmpegSource(discord.AudioSource):
    """Read PCM or unchanged Opus packets with bounded subprocess cleanup."""

    def __init__(
        self,
        executable: Path,
        source: str,
        headers: tuple[tuple[str, str], ...],
        *,
        opus: bool = False,
    ) -> None:
        arguments = _ffmpeg_arguments(executable, source, headers, opus=opus)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        if self._process.stdout is None:
            self._process.kill()
            raise OSError("FFmpeg did not create an output pipe.")
        self._stdout: BinaryIO = cast(BinaryIO, self._process.stdout)
        self._current_error: Exception | None = None
        self._cleanup_lock = threading.Lock()
        self._closed = threading.Event()
        self._packets = OggStream(self._stdout).iter_packets() if opus else None
        self._has_opus_header = False
        self.gain_db = 0.0

    @property
    def current_error(self) -> Exception | None:
        return self._current_error

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def read(self) -> bytes:
        if self._packets is None:
            data = self._stdout.read(_PCM_FRAME_BYTES)
            if data:
                return data.ljust(_PCM_FRAME_BYTES, b"\0")
        else:
            try:
                for packet in self._packets:
                    if not self._has_opus_header:
                        if (
                            not packet.startswith(b"OpusHead")
                            or len(packet) < 19
                            or packet[9] not in (1, 2)
                            or packet[18] != 0
                        ):
                            raise _MediaStreamError("Unsupported Opus stream header.")
                        self.gain_db = struct.unpack_from("<h", packet, 16)[0] / 256
                        self._has_opus_header = True
                        continue
                    if packet.startswith(b"OpusTags"):
                        continue
                    return packet
            except OggError:
                raise _MediaStreamError("Invalid Opus stream.") from None

        try:
            return_code: int | None = self._process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            return_code = self._process.poll()
        if return_code not in (None, 0):
            self._current_error = _MediaStreamError("FFmpeg stream failed.")
        return b""

    def is_opus(self) -> bool:
        return self._packets is not None

    def cleanup(self) -> None:
        with self._cleanup_lock:
            if self._closed.is_set():
                return
            try:
                if self._process.poll() is None:
                    self._process.kill()
                try:
                    self._process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
            except BaseException:
                self._stdout.close()
                raise
            else:
                self._stdout.close()
                self._closed.set()


class _VolumeSource(discord.AudioSource):
    """Copy compatible Opus at unity gain; encode only frames needing changes."""

    def __init__(self, original: _FFmpegSource, volume: float = 1.0) -> None:
        self.original = original
        self.volume = volume
        self._decoder = (
            discord.opus.Decoder()  # type: ignore[no-untyped-call]
            if original.is_opus()
            else None
        )
        self._encoder: discord.opus.Encoder | None = None
        self._pcm = bytearray()

    @property
    def _current_error(self) -> Exception | None:
        # discord.py checks this attribute after read() reaches EOF.
        return self.original.current_error

    def is_opus(self) -> bool:
        return True

    def read(self) -> bytes:
        volume = self.volume
        while len(self._pcm) < _PCM_FRAME_BYTES:
            packet = self.original.read()
            if not packet:
                if not self._pcm:
                    return b""
                self._pcm.extend(b"\0" * (_PCM_FRAME_BYTES - len(self._pcm)))
                break
            if self._decoder is None:
                pcm = packet
            else:
                # Keep decoding history current even while sending original packets.
                self._decoder.set_gain(self.original.gain_db)
                try:
                    pcm = self._decoder.decode(packet, fec=False)
                except discord.opus.OpusError:
                    raise _MediaStreamError("Invalid Opus audio packet.") from None
                if (
                    volume == 1.0
                    and self.original.gain_db == 0.0
                    and not self._pcm
                    and len(pcm) == _PCM_FRAME_BYTES
                ):
                    return packet
            self._pcm.extend(pcm)

        pcm = bytes(self._pcm[:_PCM_FRAME_BYTES])
        del self._pcm[:_PCM_FRAME_BYTES]
        if self._encoder is None:
            self._encoder = discord.opus.Encoder(
                application="audio",
                bitrate=_MUSIC_BITRATE_KBPS,
                bandwidth="full",
                signal_type="music",
                fec=False,
            )
        return self._encoder.encode(audioop.mul(pcm, 2, volume), _SAMPLES_PER_FRAME)

    def cleanup(self) -> None:
        self.original.cleanup()


@dataclass(slots=True)
class _Playback:
    source: _VolumeSource
    track: ResolvedTrack
    cancellation_error: Exception | None = None


class _DiscordClient(discord.Client):
    def __init__(self, owner: DiscordVoice, *, intents: discord.Intents) -> None:
        super().__init__(intents=intents)
        self._owner = owner

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
            and member.guild.id == self._owner.guild_id
            and after.channel is None
        ):
            previous_channel_id = (
                before.channel.id if before.channel is not None else None
            )
            if not self._owner._consume_expected_disconnect(  # pyright: ignore[reportPrivateUsage]
                previous_channel_id
            ):
                await self._owner._discord_voice_disconnected()  # pyright: ignore[reportPrivateUsage]


class DiscordVoice:
    """Voice output for one configured Discord guild."""

    def __init__(self, guild_id: int, ffmpeg_path: Path, volume: float = 1.0) -> None:
        self._validate_volume(volume)
        self.guild_id = guild_id
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
        if self._client.get_guild(self.guild_id) is None:
            raise VoiceError("The configured Discord guild is unavailable.")

    def _discord_ready(self) -> None:
        self._sent_activity = None
        if self._client.get_guild(self.guild_id) is None:
            self._startup_error = VoiceError(
                "The configured Discord guild is unavailable."
            )
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

    def _guild(self) -> discord.Guild | None:
        return self._client.get_guild(self.guild_id)

    def channels(self) -> tuple[VoiceChannelInfo, ...]:
        guild = self._guild()
        if guild is None:
            return ()
        return tuple(
            VoiceChannelInfo(
                id=channel.id,
                name=channel.name,
                can_connect=channel.permissions_for(guild.me).connect,
                can_speak=channel.permissions_for(guild.me).speak,
            )
            for channel in guild.voice_channels
        )

    def set_disconnect_handler(self, handler: Callable[[], None]) -> None:
        self._disconnect_handler = handler

    async def connect(self, channel_id: int) -> None:
        async with self._connection_lock:
            guild = self._guild()
            if guild is None:
                raise VoiceError("The configured Discord guild is unavailable.")
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                raise VoiceError(
                    "The selected voice channel is unavailable in the configured guild."
                )
            permissions = channel.permissions_for(guild.me)
            if not permissions.connect:
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

    async def _discord_voice_disconnected(self) -> None:
        async with self._connection_lock:
            voice = self._voice
            if voice is not None:
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
        self, track: ResolvedTrack, after: Callable[[Exception | None], None]
    ) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        if not track.stream_url:
            raise TrackError("The audio stream URL is empty.")
        if self._playback is not None and not self._playback.source.original.closed:
            raise VoiceError("Previous audio cleanup is still in progress.")

        try:
            pcm = _FFmpegSource(
                self._ffmpeg_path,
                track.stream_url,
                track.headers,
                opus=track.is_opus,
            )
        except FileNotFoundError as error:
            raise VoiceError(
                "The configured FFmpeg executable is unavailable."
            ) from error
        except OSError as error:
            raise VoiceError("FFmpeg could not be started.") from error

        try:
            source = _VolumeSource(pcm, volume=self._volume)
        except Exception as error:
            pcm.cleanup()
            raise VoiceError("Discord audio processing could not start.") from error
        playback = _Playback(source=source, track=track)
        self._playback = playback

        def completed(error: Exception | None) -> None:
            try:
                source.cleanup()
            except Exception:
                after(VoiceError("FFmpeg process cleanup failed."))
                return
            reported = playback.cancellation_error
            if reported is None and isinstance(error, _MediaStreamError):
                reported = TrackError("The audio stream failed.", retryable=True)
            elif reported is None and error is not None:
                reported = VoiceError("Discord audio playback failed.")
            after(reported)

        try:
            voice.play(source, after=completed)
        except Exception as error:
            source.cleanup()
            self._playback = None
            raise VoiceError("Discord audio playback could not start.") from error
        self._set_activity(track)

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
            return
        playback.cancellation_error = error
        output = self._voice if voice is None else voice
        if output is not None:
            output.stop()
        try:
            await asyncio.to_thread(playback.source.cleanup)
        except Exception as cleanup_error:
            raise VoiceError("FFmpeg process cleanup failed.") from cleanup_error
        if self._playback is playback:
            self._playback = None

    def pause(self) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        voice.pause()
        if self._playback is not None:
            self._set_activity(self._playback.track, paused=True)

    def resume(self) -> None:
        voice = self._voice
        if voice is None or not voice.is_connected():
            raise VoiceError("Discord voice is not connected.")
        voice.resume()
        if self._playback is not None:
            self._set_activity(self._playback.track)

    def set_volume(self, volume: float) -> None:
        self._validate_volume(volume)
        self._volume = volume
        if self._playback is not None:
            self._playback.source.volume = volume


def _ffmpeg_arguments(
    executable: Path,
    source: str,
    headers: tuple[tuple[str, str], ...],
    *,
    opus: bool = False,
) -> Sequence[str]:
    arguments: list[str] = [str(executable), "-nostdin"]
    if headers:
        header_lines: list[str] = []
        for name, value in headers:
            if not _HEADER_NAME.fullmatch(name) or any(
                marker in value for marker in ("\r", "\n", "\0")
            ):
                raise TrackError("The audio stream contains invalid HTTP headers.")
            header_lines.append(f"{name}: {value}")
        arguments.extend(("-headers", "\r\n".join(header_lines) + "\r\n"))
    if source.startswith(("http://", "https://")):
        arguments.extend(("-rw_timeout", "15000000"))
    arguments.extend(("-i", source, "-map", "0:a:0"))
    if opus:
        arguments.extend(("-c:a", "copy", "-f", "opus"))
    else:
        arguments.extend(("-f", "s16le", "-ar", "48000", "-ac", "2"))
    arguments.extend(("-loglevel", "warning", "pipe:1"))
    return arguments
