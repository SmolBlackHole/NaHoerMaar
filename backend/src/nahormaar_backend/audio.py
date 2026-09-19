# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Small contracts between playback, source resolution and voice output."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResolvedTrack:
    stream_url: str
    headers: tuple[tuple[str, str], ...] = ()
    is_opus: bool = False
    title: str | None = None
    uploader: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceChannelInfo:
    id: int
    name: str
    can_connect: bool
    can_speak: bool


class TrackError(RuntimeError):
    """An unavailable track or a failed stream, with a bounded retry policy."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class VoiceError(RuntimeError):
    """A voice connection or audio dependency failed."""


class SourceResolver(Protocol):
    async def resolve(self, source_url: str) -> ResolvedTrack: ...


class VoiceOutput(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def channel_id(self) -> int | None: ...

    def channels(self) -> tuple[VoiceChannelInfo, ...]: ...

    def set_disconnect_handler(self, handler: Callable[[], None]) -> None: ...

    async def connect(self, channel_id: int) -> None: ...

    async def disconnect(self) -> None: ...

    def play(
        self, track: ResolvedTrack, after: Callable[[Exception | None], None]
    ) -> None: ...

    async def stop(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def set_volume(self, volume: float) -> None: ...
