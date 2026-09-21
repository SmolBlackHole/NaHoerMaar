# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Small contracts between playback, source resolution and voice output."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ..domain.models import TrackMetadata


@dataclass(frozen=True, slots=True)
class ResolvedTrack:
    stream_url: str
    headers: tuple[tuple[str, str], ...] = ()
    is_opus: bool = False
    title: str | None = None
    uploader: str | None = None
    video_id: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None
    artist: str | None = None
    uploader_url: str | None = None

    @property
    def metadata(self) -> TrackMetadata:
        return TrackMetadata(
            self.video_id,
            self.title,
            self.uploader,
            self.duration_seconds,
            self.thumbnail_url,
            self.artist,
            self.uploader_url,
        )


@dataclass(frozen=True, slots=True)
class VoiceChannelInfo:
    id: int
    name: str
    can_connect: bool
    can_speak: bool
    guild_id: int
    guild_name: str


class TrackError(RuntimeError):
    """An unavailable track or a failed stream, with a bounded retry policy."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class VoiceError(RuntimeError):
    """A voice connection or audio dependency failed."""


class AudioEndReason(StrEnum):
    NATURAL = "natural"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"
    OUTPUT_FAILED = "output_failed"


@dataclass(frozen=True, slots=True)
class AudioStarted:
    attempt_id: UUID
    position_seconds: float


@dataclass(frozen=True, slots=True)
class AudioCompleted:
    attempt_id: UUID
    reason: AudioEndReason
    position_seconds: float
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class VoiceDisconnected:
    attempt_id: UUID | None
    channel_id: int | None
    position_seconds: float
    paused: bool


type AudioEvent = AudioStarted | AudioCompleted


class SourceResolver(Protocol):
    async def resolve(self, source_url: str) -> ResolvedTrack: ...


class MetadataResolver(Protocol):
    async def metadata(self, source_url: str) -> TrackMetadata: ...


class VoiceOutput(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def channel_id(self) -> int | None: ...

    @property
    def position_seconds(self) -> float | None: ...

    def channels(self) -> tuple[VoiceChannelInfo, ...]: ...

    def set_disconnect_handler(
        self, handler: Callable[[VoiceDisconnected], None]
    ) -> None: ...

    async def connect(self, channel_id: int) -> None: ...

    async def disconnect(self) -> None: ...

    async def close(self) -> None: ...

    def play(
        self,
        track: ResolvedTrack,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None: ...

    async def stop(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def set_volume(self, volume: float) -> None: ...

    @property
    def transitioning(self) -> bool: ...

    async def prepare_next(
        self, track: ResolvedTrack, seconds: float, on_due: Callable[[], None]
    ) -> bool: ...

    async def discard_next(self) -> None: ...

    def start_transition(
        self,
        track: ResolvedTrack,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        on_faded: Callable[[], None],
    ) -> bool: ...
