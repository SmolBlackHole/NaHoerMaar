# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Ephemeral audio inputs, correlated output facts and transport capabilities.

Adapters report facts; they never advance the queue or create playback records.
Callbacks may originate on an audio thread. Their receiver must schedule delivery
on the owning Session's loop, not mutate state from the callback.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Protocol
from uuid import UUID

from .domain.catalog import TrackFinding


@dataclass(frozen=True, slots=True)
class PlayableSource:
    """Resolve shortly before use. Never persist or send URLs/headers to clients."""

    track: TrackFinding
    stream_url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    is_opus: bool = False

    def __post_init__(self) -> None:
        if not self.stream_url or self.stream_url != self.stream_url.strip():
            raise ValueError("stream_url must be non-empty and trimmed.")
        if type(self.headers) is not tuple or any(
            type(header) is not tuple for header in self.headers
        ):
            raise ValueError("Headers must be immutable pairs.")


class AudioError(RuntimeError):
    """Source preparation or output failed; retry is a controller decision."""


class AudioSourceNotReady(AudioError):
    """The resolved source produced no first frame."""


class VoiceError(RuntimeError):
    """The requested voice connection could not be established or maintained."""


class AudioEndReason(StrEnum):
    NATURAL = "natural"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"
    OUTPUT_FAILED = "output_failed"


@dataclass(frozen=True, slots=True)
class AudioPosition:
    """Position measured from output, associated with one technical attempt."""

    attempt_id: UUID
    position_seconds: float

    def __post_init__(self) -> None:
        if not isfinite(self.position_seconds) or self.position_seconds < 0:
            raise ValueError("Audio position must be finite and non-negative.")


@dataclass(frozen=True, slots=True)
class AudioProgress(AudioPosition):
    paused: bool = False
    transitioning: bool = False


@dataclass(frozen=True, slots=True)
class AudioStarted(AudioPosition):
    """First output was confirmed, not merely selected, prepared or scheduled."""


@dataclass(frozen=True, slots=True)
class AudioCompleted(AudioPosition):
    reason: AudioEndReason
    error: Exception | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CrossfadeDue:
    outgoing_attempt_id: UUID
    preparation_id: UUID
    duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class CrossfadeCompleted:
    attempt_id: UUID
    preparation_id: UUID


type AudioEvent = AudioStarted | AudioCompleted | CrossfadeDue | CrossfadeCompleted


class AudioPlayer(Protocol):
    """Own decoding/mixing resources. Calls must not block the asyncio loop.

    Preparation/activation target explicit IDs. A mismatched target must leave
    newer output and prepared audio intact. Stop affects both voices of a fade;
    pause/resume also affect both. Async work settles cleanup on cancellation.
    Progress is an atomic snapshot; None means no output attempt is present.
    The composition root closes each adapter once, even if it also serves voice.
    """

    @property
    def progress(self) -> AudioProgress | None: ...

    async def play(
        self,
        source: PlayableSource,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None: ...

    async def stop(self, attempt_id: UUID) -> None: ...

    def pause(self, attempt_id: UUID) -> None: ...

    def resume(self, attempt_id: UUID) -> None: ...

    def set_volume(self, volume: float) -> None: ...

    async def prepare_next(
        self,
        source: PlayableSource,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        seconds: float,
        notify: Callable[[AudioEvent], None],
    ) -> bool: ...

    async def discard_next(self, preparation_id: UUID) -> None: ...

    def start_transition(
        self,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
    ) -> bool:
        """Activate already prepared audio; success is not output confirmation."""
        ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class VoiceChannel:
    id: int
    name: str
    guild_id: int
    guild_name: str
    can_connect: bool
    can_speak: bool


@dataclass(frozen=True, slots=True)
class VoiceConnection:
    connection_id: UUID
    channel_id: int


@dataclass(frozen=True, slots=True)
class VoiceDisconnected:
    connection: VoiceConnection
    progress: AudioProgress | None


class VoiceTransport(Protocol):
    @property
    def connection(self) -> VoiceConnection | None:
        """Confirmed connection identity, not the persisted reconnect target."""
        ...

    def channels(self) -> tuple[VoiceChannel, ...]: ...

    def set_disconnect_handler(
        self, handler: Callable[[VoiceDisconnected], None]
    ) -> None: ...

    async def connect(self, channel_id: int, connection_id: UUID) -> None:
        """Return only when ready. This operation does not resume audio itself."""
        ...

    async def disconnect(self, connection_id: UUID) -> None:
        """Disconnect this connection only; stale work cannot close a newer join."""
        ...

    async def close(self) -> None: ...
