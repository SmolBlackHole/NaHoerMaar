# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Filling policies choose candidates; the Session owns I/O and queue commits."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import UUID, uuid4

from .catalog import MediaReference
from .queue import Contributor, QueueEntry

RADIO_TARGET = 3
RADIO_POOL = 50


@dataclass(frozen=True, slots=True)
class StartRadio:
    seed: MediaReference
    expected_generation: UUID | None
    initial_tracks: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class StopRadio:
    expected_generation: UUID


@dataclass(frozen=True, slots=True)
class RetryRadio:
    expected_generation: UUID


class RadioState(StrEnum):
    ACTIVE = "active"
    LOADING = "loading"
    WAITING = "waiting"


@dataclass(frozen=True, slots=True)
class RadioRequest:
    generation: UUID
    seed: MediaReference
    continuation: str | None
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class RadioLoaded:
    generation: UUID
    request_id: UUID
    track_ids: tuple[UUID, ...]
    continuation: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ManualStrategy:
    generation: UUID | None = None

    def queue_next(
        self,
        upcoming: tuple[QueueEntry, ...],
        excluded: frozenset[UUID],
        *,
        paused: bool,
    ) -> tuple[ManualStrategy, tuple[UUID, ...], RadioRequest | None]:
        return self, (), None


@dataclass(frozen=True, slots=True)
class RadioStrategy:
    seed: MediaReference
    initiator: Contributor
    generation: UUID = field(default_factory=uuid4)
    state: RadioState = RadioState.ACTIVE
    pool: tuple[UUID, ...] = ()
    excluded: frozenset[UUID] = frozenset()
    continuation: str | None = None
    request_id: UUID | None = None
    error: str | None = None

    def queue_next(
        self,
        upcoming: tuple[QueueEntry, ...],
        excluded: frozenset[UUID],
        *,
        paused: bool,
    ) -> tuple[RadioStrategy, tuple[UUID, ...], RadioRequest | None]:
        if paused or self.state is not RadioState.ACTIVE:
            return self, (), None
        missing = max(0, RADIO_TARGET - len(upcoming))
        if not missing:
            return self, (), None
        blocked = excluded | self.excluded | {entry.track_id for entry in upcoming}
        candidates = tuple(
            dict.fromkeys(track_id for track_id in self.pool if track_id not in blocked)
        )
        selected, remaining = candidates[:missing], candidates[missing:]
        strategy = replace(self, pool=remaining)
        request = None
        if len(selected) < missing:
            request = RadioRequest(self.generation, self.seed, self.continuation)
            strategy = replace(
                strategy, state=RadioState.LOADING, request_id=request.id, error=None
            )
        return strategy, selected, request

    def loaded(self, message: RadioLoaded, excluded: frozenset[UUID]) -> RadioStrategy:
        if (
            message.generation != self.generation
            or message.request_id != self.request_id
            or self.state is not RadioState.LOADING
        ):
            return self
        blocked = self.excluded | excluded
        candidates = tuple(
            dict.fromkeys(
                track_id
                for track_id in message.track_ids[:RADIO_POOL]
                if track_id not in blocked
            )
        )
        error = message.error or (
            None if candidates else "No new recommendations available. Try again later."
        )
        return replace(
            self,
            pool=candidates,
            continuation=message.continuation,
            request_id=None,
            state=RadioState.WAITING if error else RadioState.ACTIVE,
            error=error,
        )
