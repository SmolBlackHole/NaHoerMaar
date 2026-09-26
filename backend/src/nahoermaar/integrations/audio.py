# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""FFmpeg sources, bounded buffering and sample-clock-driven transitions."""

from __future__ import annotations

import audioop
import logging
import re
import struct
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import cos, isfinite, pi
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
        (b"unrecognized option", "unsupported_option"),
        (b"option not found", "unsupported_option"),
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
                "-reconnect_delay_max",
                "2",
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


FRAME_SECONDS = 0.02
BUFFER_FRAMES = 400
PREPARE_TIMEOUT = 15.0
_SLOW_READ_SECONDS = 1.0
_SILENCE = AudioFrame(bytes(3840))


@dataclass(frozen=True, slots=True)
class AudioDiagnostics:
    first_frame_seconds: float | None = None
    underrun_count: int = 0
    stalled_seconds: float = 0
    max_stall_seconds: float = 0


class BufferedAudio:
    """One reader owns decoding; at most eight seconds are retained in memory."""

    def __init__(self, source: VolumeSource) -> None:
        self.source = source
        self._frames: deque[AudioFrame] = deque()
        self._condition = threading.Condition()
        self._stopped = False
        self._ended = False
        self._error: Exception | None = None
        self._started_at = time.monotonic()
        self._read_started_at: float | None = None
        self._first_frame_seen = False
        self._first_frame_seconds: float | None = None
        self._cleanup_lock = threading.Lock()
        self._thread = threading.Thread(target=self._produce, daemon=True)
        self._thread.start()

    def _produce(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._stopped or len(self._frames) < BUFFER_FRAMES
                    )
                    if self._stopped:
                        return
                    read_started_at = time.monotonic()
                    self._read_started_at = read_started_at
                frame = self.source.read_frame()
                read_seconds = time.monotonic() - read_started_at
                first_frame_seconds: float | None = None
                with self._condition:
                    self._read_started_at = None
                    if frame is None:
                        self._error = self.source.original.current_error
                    else:
                        self._frames.append(frame)
                        if not self._first_frame_seen:
                            self._first_frame_seen = True
                            first_frame_seconds = time.monotonic() - self._started_at
                            self._first_frame_seconds = first_frame_seconds
                        self._condition.notify_all()
                if first_frame_seconds is not None:
                    _LOGGER.info(
                        "audio.buffer.first_frame audio_pid=%s elapsed=%.3f",
                        self.process_id,
                        first_frame_seconds,
                    )
                if read_seconds >= _SLOW_READ_SECONDS:
                    _LOGGER.warning(
                        "audio.buffer.read_slow audio_pid=%s seconds=%.3f eof=%s",
                        self.process_id,
                        read_seconds,
                        frame is None,
                    )
                if frame is None:
                    return
        except Exception as failure:
            with self._condition:
                if not self._stopped:
                    self._error = failure
        finally:
            with self._condition:
                self._read_started_at = None
                self._ended = True
                error = self._error
                buffered_seconds = len(self._frames) * FRAME_SECONDS
                self._condition.notify_all()
            if error is not None:
                _LOGGER.warning(
                    "audio.buffer.failed audio_pid=%s buffered_seconds=%.3f error=%s",
                    getattr(self.source.original, "process_id", None),
                    buffered_seconds,
                    type(error).__name__,
                )

    def wait_ready(self, frames: int) -> bool:
        with self._condition:
            self._condition.wait_for(
                lambda: len(self._frames) >= frames or self._ended or self._stopped,
                timeout=PREPARE_TIMEOUT,
            )
            ready = (
                not self._stopped
                and self._error is None
                and len(self._frames) >= frames
            )
            stopped = self._stopped
            buffered_seconds = len(self._frames) * FRAME_SECONDS
            ended = self._ended
            reader_wait_seconds = (
                f"{time.monotonic() - self._read_started_at:.3f}"
                if self._read_started_at is not None
                else "idle"
            )
            error = type(self._error).__name__ if self._error else None
        if not ready and not stopped:
            _LOGGER.warning(
                "audio.buffer.not_ready audio_pid=%s required_seconds=%.3f "
                "buffered_seconds=%.3f ended=%s reader_wait_seconds=%s error=%s",
                self.process_id,
                frames * FRAME_SECONDS,
                buffered_seconds,
                ended,
                reader_wait_seconds,
                error,
            )
        return ready

    @property
    def process_id(self) -> int | None:
        return getattr(self.source.original, "process_id", None)

    @property
    def reader_wait_seconds(self) -> float | None:
        with self._condition:
            return (
                time.monotonic() - self._read_started_at
                if self._read_started_at is not None
                else None
            )

    @property
    def first_frame_seconds(self) -> float | None:
        with self._condition:
            return self._first_frame_seconds

    @property
    def ended(self) -> bool:
        with self._condition:
            return self._ended and not self._frames

    def take(self, timeout: float = FRAME_SECONDS) -> AudioFrame | None:
        with self._condition:
            self._condition.wait_for(
                lambda: bool(self._frames) or self._ended or self._stopped,
                timeout=timeout,
            )
            if self._frames:
                frame = self._frames.popleft()
                self._condition.notify_all()
                return frame
            if self._error is not None:
                raise MediaStreamError(
                    "The buffered audio stream failed."
                ) from self._error
            return None

    def cleanup(self) -> None:
        with self._cleanup_lock:
            with self._condition:
                self._stopped = True
                self._frames.clear()
                self._condition.notify_all()
            self.source.cleanup()
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                _LOGGER.warning(
                    "audio.buffer.cleanup_timeout audio_pid=%s reader_wait_seconds=%s",
                    self.process_id,
                    self.reader_wait_seconds,
                )
                raise RuntimeError("The audio reader did not stop.")


@dataclass(slots=True)
class _Prepared:
    audio: BufferedAudio
    frames: int
    trigger: float
    on_due: Callable[[], None]
    requested: bool = False


class CrossfadeSource(discord.AudioSource):
    """Only the Discord audio thread advances either track or the fade clock."""

    def __init__(
        self,
        current: BufferedAudio,
        *,
        volume: float,
        position: float = 0,
        paused: bool = False,
        on_started: Callable[[], None] = lambda: None,
    ) -> None:
        self.current = current
        self.volume = volume
        self._position = position
        self._paused = paused
        self._encoder = FrameEncoder()
        self._lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._closed = False
        self._stopping = False
        self._ended = False
        self._prepared: _Prepared | None = None
        self._outgoing: BufferedAudio | None = None
        self._retiring: BufferedAudio | None = None
        self._retire_thread: threading.Thread | None = None
        self._fade_frames = 0
        self._fade_index = 0
        self._on_faded: Callable[[], None] = lambda: None
        self._starved_at: float | None = None
        self._underrun_count = 0
        self._stalled_seconds = 0.0
        self._max_stall_seconds = 0.0
        self._current_error: Exception | None = None
        self._on_started: Callable[[], None] | None = on_started

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def position_seconds(self) -> float:
        with self._lock:
            return self._position

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def current_error(self) -> Exception | None:
        with self._lock:
            return self._current_error

    @property
    def diagnostics(self) -> AudioDiagnostics:
        with self._lock:
            active_stall = (
                time.monotonic() - self._starved_at
                if self._starved_at is not None
                else 0.0
            )
            return AudioDiagnostics(
                first_frame_seconds=self.current.first_frame_seconds,
                underrun_count=self._underrun_count,
                stalled_seconds=self._stalled_seconds + active_stall,
                max_stall_seconds=max(self._max_stall_seconds, active_stall),
            )

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    @property
    def transitioning(self) -> bool:
        with self._lock:
            return self._outgoing is not None or self._retiring is not None

    def is_opus(self) -> bool:
        return True

    def stage(
        self,
        audio: BufferedAudio,
        *,
        seconds: float,
        duration: float,
        on_due: Callable[[], None],
    ) -> bool:
        with self._lock:
            if self._stopping or self._ended or self.transitioning or self._prepared:
                return False
            self._prepared = _Prepared(
                audio,
                max(1, round(seconds / FRAME_SECONDS)),
                duration - seconds,
                on_due,
            )
            return True

    def discard(self) -> BufferedAudio | None:
        with self._lock:
            prepared, self._prepared = self._prepared, None
            return prepared.audio if prepared else None

    def activate(
        self,
        on_faded: Callable[[], None],
        on_activate: Callable[[], None] = lambda: None,
        on_started: Callable[[], None] = lambda: None,
        *,
        immediate: bool = False,
    ) -> bool:
        with self._lock:
            prepared = self._prepared
            if (
                self._stopping
                or self._ended
                or prepared is None
                or (not immediate and not prepared.requested)
            ):
                return False
            self._prepared = None
            self._outgoing = self.current
            self.current = prepared.audio
            self._position = 0
            self._fade_frames = 1 if immediate else prepared.frames
            self._fade_index = 0
            self._starved_at = None
            self._underrun_count = 0
            self._stalled_seconds = 0.0
            self._max_stall_seconds = 0.0
            self._on_faded = on_faded
            self._on_started = on_started
            # Bind completion to the incoming attempt before its first read can
            # fail on the audio thread.
            on_activate()
            return True

    def read(self) -> bytes:
        with self._lock:
            if self._stopping or self._ended:
                return b""
            if self._paused:
                return self._encoder.encode(_SILENCE, self.volume)
            prepared = self._prepared
            if (
                prepared
                and not prepared.requested
                and self._position >= prepared.trigger
            ):
                prepared.requested = True
                prepared.on_due()
            try:
                frame = self.current.take()
                if frame is None:
                    if self.current.ended:
                        self._ended = True
                        return b""
                    now = time.monotonic()
                    if self._starved_at is None:
                        self._starved_at = now
                        self._underrun_count += 1
                        _LOGGER.warning(
                            "audio.underrun audio_pid=%s position=%.3f fading=%s "
                            "reader_wait_seconds=%s",
                            getattr(self.current, "process_id", None),
                            self._position,
                            self._outgoing is not None,
                            getattr(self.current, "reader_wait_seconds", None),
                        )
                    if now - self._starved_at >= PREPARE_TIMEOUT:
                        raise MediaStreamError("The audio stream timed out.")
                    return self._encoder.encode(_SILENCE, self.volume)
                if self._starved_at is not None:
                    stalled_seconds = time.monotonic() - self._starved_at
                    self._stalled_seconds += stalled_seconds
                    self._max_stall_seconds = max(
                        self._max_stall_seconds, stalled_seconds
                    )
                    _LOGGER.info(
                        "audio.recovered audio_pid=%s position=%.3f stalled_seconds=%.3f",
                        getattr(self.current, "process_id", None),
                        self._position,
                        stalled_seconds,
                    )
                self._starved_at = None
                self._position += FRAME_SECONDS
                outgoing = self._outgoing
                if outgoing is not None:
                    try:
                        tail = outgoing.take(timeout=0)
                    except MediaStreamError:
                        tail = None
                    self._fade_index += 1
                    weight = (1 - cos(pi * self._fade_index / self._fade_frames)) / 2
                    # Missing tail frames do not cancel the fade or jump the
                    # incoming track to full volume. A temporary underrun can
                    # recover; an early EOF still completes the same envelope.
                    frame = AudioFrame(
                        audioop.add(
                            audioop.mul((tail or _SILENCE).pcm, 2, 1 - weight),
                            audioop.mul(frame.pcm, 2, weight),
                            2,
                        )
                    )
                    if self._fade_index >= self._fade_frames:
                        self._outgoing = None
                        self._retiring = outgoing
                        self._retire_thread = threading.Thread(
                            target=self._retire,
                            args=(outgoing,),
                            daemon=True,
                        )
                        self._retire_thread.start()
                packet = self._encoder.encode(frame, self.volume)
                on_started, self._on_started = self._on_started, None
                if on_started is not None:
                    on_started()
                return packet
            except Exception as error:
                _LOGGER.warning(
                    "audio.failed audio_pid=%s position=%.3f fading=%s error=%s",
                    getattr(self.current, "process_id", None),
                    self._position,
                    self._outgoing is not None,
                    type(error).__name__,
                )
                self._current_error = error
                self._ended = True
                raise

    def _retire(self, audio: BufferedAudio) -> None:
        cleaned = False
        try:
            audio.cleanup()
            cleaned = True
        except Exception as error:
            with self._lock:
                self._current_error = error
                self._ended = True
        finally:
            with self._lock:
                if cleaned:
                    self._retiring = None
                callback = self._on_faded
                closed = self._stopping
            if not closed:
                callback()

    def cleanup(self) -> None:
        with self._cleanup_lock:
            with self._lock:
                self._stopping = True
                prepared = self._prepared.audio if self._prepared else None
                sources = (self.current, self._outgoing, self._retiring, prepared)
                retire_thread = self._retire_thread
            error: Exception | None = None
            for source in sources:
                if source is not None:
                    try:
                        source.cleanup()
                    except Exception as caught:
                        error = caught
            if retire_thread is not None:
                retire_thread.join(timeout=3.0)
                if retire_thread.is_alive():
                    raise RuntimeError("Outgoing audio cleanup did not stop.")
            if error is not None:
                raise error
            with self._lock:
                self._prepared = None
                self._closed = True
