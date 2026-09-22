# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded frame buffering and natural, sample-clock-driven transitions."""

from __future__ import annotations

import audioop
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from math import cos, pi

import discord

from .audio_sources import AudioFrame, FrameEncoder, MediaStreamError, VolumeSource

FRAME_SECONDS = 0.02
BUFFER_FRAMES = 400
PREPARE_TIMEOUT = 15.0
_SLOW_READ_SECONDS = 1.0
_SILENCE = AudioFrame(bytes(3840))
_LOGGER = logging.getLogger(__name__)


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
                with self._condition:
                    self._read_started_at = None
                    if frame is None:
                        self._error = self.source.original.current_error
                    else:
                        self._frames.append(frame)
                        self._condition.notify_all()
                if frame is not None and not self._first_frame_seen:
                    self._first_frame_seen = True
                    _LOGGER.info(
                        "audio.buffer.first_frame audio_pid=%s elapsed=%.3f",
                        self.process_id,
                        time.monotonic() - self._started_at,
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
        except Exception as error:
            with self._condition:
                if not self._stopped:
                    self._error = error
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
    ) -> bool:
        with self._lock:
            prepared = self._prepared
            if (
                self._stopping
                or self._ended
                or prepared is None
                or not prepared.requested
            ):
                return False
            self._prepared = None
            self._outgoing = self.current
            self.current = prepared.audio
            self._position = 0
            self._fade_frames = prepared.frames
            self._fade_index = 0
            self._starved_at = None
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
                    _LOGGER.info(
                        "audio.recovered audio_pid=%s position=%.3f stalled_seconds=%.3f",
                        getattr(self.current, "process_id", None),
                        self._position,
                        time.monotonic() - self._starved_at,
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
