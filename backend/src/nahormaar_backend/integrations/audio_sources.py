# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""FFmpeg sources, frame decoding and Opus passthrough."""

from __future__ import annotations

import audioop
import logging
import re
import struct
import subprocess
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import BinaryIO, cast

import discord
from discord.oggparse import OggError, OggStream


_PCM_FRAME_BYTES = 3_840
_SAMPLES_PER_FRAME = 960
_MUSIC_BITRATE_KBPS = 512
_PROCESS_TIMEOUT_SECONDS = 2.0
_DURATION_TOLERANCE_SECONDS = 1.0
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HTTP_ERROR = re.compile(rb"(?:HTTP error|Server returned)\s+([45][0-9]{2})", re.I)
_LOGGER = logging.getLogger(__name__)


def _diagnostic_kind(line: bytes) -> str:
    """Keep only known failure categories, never URLs, headers or raw stderr."""
    if match := _HTTP_ERROR.search(line):
        return "http_" + match[1].decode("ascii")
    lowered = line.lower()
    for marker, kind in (
        (b"timed out", "timeout"),
        (b"connection reset", "connection_reset"),
        (b"connection refused", "connection_refused"),
        (b"resolve hostname", "dns_failure"),
        (b"invalid data", "invalid_media"),
        (b"error during demuxing", "demux_error"),
        (b"error decoding", "decode_error"),
        (b"input/output error", "io_error"),
        (b"tls", "tls_error"),
        (b"immediate exit requested", "interrupted"),
        (b"corrupt input packet", "corrupt_media"),
    ):
        if marker in lowered:
            return kind
    if b"error" in lowered:
        return "ffmpeg_error"
    return "ffmpeg_message"


class MediaStreamError(RuntimeError):
    """Internal marker for a media-specific FFmpeg failure."""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """Twenty milliseconds of stereo PCM, optionally with its original packet."""

    pcm: bytes
    opus: bytes | None = None


class FrameEncoder:
    def __init__(self) -> None:
        self._encoder: discord.opus.Encoder | None = None

    def encode(self, frame: AudioFrame, volume: float) -> bytes:
        if frame.opus is not None and volume == 1.0:
            return frame.opus
        if self._encoder is None:
            self._encoder = discord.opus.Encoder(
                application="audio",
                bitrate=_MUSIC_BITRATE_KBPS,
                bandwidth="full",
                signal_type="music",
                fec=False,
            )
        return self._encoder.encode(
            audioop.mul(frame.pcm, 2, volume), _SAMPLES_PER_FRAME
        )


class FFmpegSource(discord.AudioSource):
    """Read PCM or unchanged Opus packets with bounded subprocess cleanup."""

    def __init__(
        self,
        executable: Path,
        source: str,
        headers: tuple[tuple[str, str], ...],
        *,
        opus: bool = False,
        position_seconds: float = 0,
        duration_seconds: float | None = None,
    ) -> None:
        if duration_seconds is not None and (
            not isfinite(duration_seconds) or duration_seconds < 0
        ):
            raise ValueError("Track duration must be finite and non-negative.")
        arguments = _ffmpeg_arguments(
            executable, source, headers, opus=opus, position_seconds=position_seconds
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        if self._process.stdout is None:
            self._process.kill()
            raise OSError("FFmpeg did not create an output pipe.")
        self._stdout: BinaryIO = cast(BinaryIO, self._process.stdout)
        self._pid = self._process.pid
        self._started_at = time.monotonic()
        self._position_seconds = position_seconds
        self._duration_seconds = duration_seconds
        self._output_seconds = 0.0
        self._diagnostics: deque[str] = deque(maxlen=8)
        self._reported_diagnostics: set[str] = set()
        self._diagnostics_lock = threading.Lock()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._current_error: Exception | None = None
        self._cleanup_lock = threading.Lock()
        self._closed = threading.Event()
        self._packets = OggStream(self._stdout).iter_packets() if opus else None
        self._has_opus_header = False
        self.gain_db = 0.0
        _LOGGER.info(
            "ffmpeg.started audio_pid=%s format=%s remote=%s position=%.3f",
            self._pid,
            "opus" if opus else "pcm",
            source.startswith(("http://", "https://")),
            position_seconds,
        )

    def _drain_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        try:
            with stream:
                while line := stream.readline(4096):
                    kind = _diagnostic_kind(line)
                    with self._diagnostics_lock:
                        if not self._diagnostics or self._diagnostics[-1] != kind:
                            self._diagnostics.append(kind)
                        report = (
                            kind != "ffmpeg_message"
                            and kind not in self._reported_diagnostics
                        )
                        if report:
                            self._reported_diagnostics.add(kind)
                    if report:
                        _LOGGER.warning(
                            "ffmpeg.diagnostic audio_pid=%s kind=%s",
                            self._pid,
                            kind,
                        )
        except OSError as error:
            _LOGGER.warning(
                "ffmpeg.diagnostics_failed audio_pid=%s error=%s",
                self._pid,
                type(error).__name__,
            )

    def _diagnostic_summary(self) -> str:
        with self._diagnostics_lock:
            return ",".join(self._diagnostics) or "none"

    def _terminal_diagnostic(self) -> bool:
        with self._diagnostics_lock:
            return any(kind != "ffmpeg_message" for kind in self._diagnostics)

    def _ended_early(self) -> bool:
        duration = self._duration_seconds
        if duration is None:
            return False
        expected = max(0.0, duration - self._position_seconds)
        return self._output_seconds + _DURATION_TOLERANCE_SECONDS < expected

    @property
    def process_id(self) -> int:
        return self._pid

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
                self._output_seconds += len(data) / (48_000 * 2 * 2)
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
                            raise MediaStreamError("Unsupported Opus stream header.")
                        self.gain_db = struct.unpack_from("<h", packet, 16)[0] / 256
                        self._has_opus_header = True
                        continue
                    if packet.startswith(b"OpusTags"):
                        continue
                    self._output_seconds += (
                        discord.opus.Decoder.packet_get_samples_per_frame(packet)
                        * discord.opus.Decoder.packet_get_nb_frames(packet)
                        / 48_000
                    )
                    return packet
            except OggError:
                raise MediaStreamError("Invalid Opus stream.") from None

        try:
            return_code: int | None = self._process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            return_code = self._process.poll()
        self._stderr_thread.join(timeout=0.2)
        ended_early = self._ended_early()
        recovered = self._duration_seconds is not None and not ended_early
        if (
            return_code not in (None, 0)
            or ended_early
            or (self._terminal_diagnostic() and not recovered)
        ):
            self._current_error = MediaStreamError("FFmpeg stream failed.")
        _LOGGER.log(
            logging.WARNING if self._current_error else logging.INFO,
            "ffmpeg.eof audio_pid=%s returncode=%s elapsed=%.3f "
            "output_seconds=%.3f expected_seconds=%s diagnostics=%s",
            self._pid,
            return_code,
            time.monotonic() - self._started_at,
            self._output_seconds,
            (
                f"{max(0.0, self._duration_seconds - self._position_seconds):.3f}"
                if self._duration_seconds is not None
                else "unknown"
            ),
            self._diagnostic_summary(),
        )
        return b""

    def is_opus(self) -> bool:
        return self._packets is not None

    def cleanup(self) -> None:
        with self._cleanup_lock:
            if self._closed.is_set():
                return
            killed = False
            try:
                if self._process.poll() is None:
                    killed = True
                    self._process.kill()
                try:
                    self._process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    killed = True
                    self._process.kill()
                    self._process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
            except BaseException:
                self._stdout.close()
                raise
            else:
                self._stdout.close()
                self._stderr_thread.join(timeout=_PROCESS_TIMEOUT_SECONDS)
                self._closed.set()
                _LOGGER.info(
                    "ffmpeg.closed audio_pid=%s killed=%s returncode=%s diagnostics=%s",
                    self._pid,
                    killed,
                    self._process.poll(),
                    self._diagnostic_summary(),
                )


class VolumeSource(discord.AudioSource):
    """Copy compatible Opus at unity gain; encode only frames needing changes."""

    def __init__(self, original: FFmpegSource, volume: float = 1.0) -> None:
        self.original = original
        self.volume = volume
        self._decoder = (
            discord.opus.Decoder()  # type: ignore[no-untyped-call]
            if original.is_opus()
            else None
        )
        self._encoder = FrameEncoder()
        self._pcm = bytearray()

    @property
    def _current_error(self) -> Exception | None:
        # discord.py checks this attribute after read() reaches EOF.
        return self.original.current_error

    def is_opus(self) -> bool:
        return True

    def read(self) -> bytes:
        frame = self.read_frame()
        return self._encoder.encode(frame, self.volume) if frame is not None else b""

    def read_frame(self) -> AudioFrame | None:
        while len(self._pcm) < _PCM_FRAME_BYTES:
            packet = self.original.read()
            if not packet:
                if not self._pcm:
                    return None
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
                    raise MediaStreamError("Invalid Opus audio packet.") from None
                if (
                    self.original.gain_db == 0.0
                    and not self._pcm
                    and len(pcm) == _PCM_FRAME_BYTES
                ):
                    return AudioFrame(pcm, packet)
            self._pcm.extend(pcm)

        pcm = bytes(self._pcm[:_PCM_FRAME_BYTES])
        del self._pcm[:_PCM_FRAME_BYTES]
        return AudioFrame(pcm)

    def cleanup(self) -> None:
        self.original.cleanup()


def _ffmpeg_arguments(
    executable: Path,
    source: str,
    headers: tuple[tuple[str, str], ...],
    *,
    opus: bool = False,
    position_seconds: float = 0,
) -> Sequence[str]:
    if not isfinite(position_seconds) or position_seconds < 0:
        raise ValueError("Playback position must be finite and non-negative.")
    arguments: list[str] = [str(executable), "-nostdin"]
    if headers:
        header_lines: list[str] = []
        for name, value in headers:
            if not _HEADER_NAME.fullmatch(name) or any(
                marker in value for marker in ("\r", "\n", "\0")
            ):
                raise ValueError("The audio stream contains invalid HTTP headers.")
            header_lines.append(f"{name}: {value}")
        arguments.extend(("-headers", "\r\n".join(header_lines) + "\r\n"))
    if source.startswith(("http://", "https://")):
        arguments.extend(
            (
                "-rw_timeout",
                "15000000",
                "-reconnect",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_max_retries",
                "2",
                "-reconnect_delay_max",
                "2",
                "-reconnect_delay_total_max",
                "3",
            )
        )
    if position_seconds:
        arguments.extend(("-ss", str(position_seconds)))
    arguments.extend(("-i", source, "-map", "0:a:0"))
    if position_seconds and opus:
        # Input seeking can retain packets before the target when stream-copying.
        arguments.extend(("-ss", "0"))
    if opus:
        arguments.extend(("-c:a", "copy", "-f", "opus"))
    else:
        arguments.extend(("-f", "s16le", "-ar", "48000", "-ac", "2"))
    arguments.extend(("-loglevel", "warning", "pipe:1"))
    return arguments
