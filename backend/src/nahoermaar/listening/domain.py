# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Durable playback, presence and listening facts."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import NewType
from uuid import UUID

from nahoermaar.player.domain import ListeningSessionId, TrackRequestId
from nahoermaar.users.domain import UserId

PlaybackRecordId = NewType("PlaybackRecordId", UUID)
ListenerPresenceId = NewType("ListenerPresenceId", UUID)


def _aware(value: datetime, label: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")


class PlaybackEndReason(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    STOPPED = "stopped"
    FAILED = "failed"


class ListeningErrorCode(StrEnum):
    AUDIENCE_SESSION_MISMATCH = "audience_session_mismatch"
    PLAYBACK_CONFLICT = "playback_conflict"
    PLAYBACK_NOT_FOUND = "playback_not_found"
    PROGRESS_REGRESSION = "progress_regression"


class ListeningError(RuntimeError):
    def __init__(self, code: ListeningErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlaybackRecord:
    id: PlaybackRecordId
    session_id: ListeningSessionId
    request_id: TrackRequestId
    started_at: datetime
    audio_seconds: float = 0.0
    group_audio_seconds: float = 0.0
    ended_at: datetime | None = None
    end_reason: PlaybackEndReason | None = None

    def __post_init__(self) -> None:
        _aware(self.started_at, "Playback start")
        if not isfinite(self.audio_seconds) or self.audio_seconds < 0:
            raise ValueError("Playback audio time must be finite and non-negative.")
        if (
            not isfinite(self.group_audio_seconds)
            or self.group_audio_seconds < 0
            or self.group_audio_seconds > self.audio_seconds
        ):
            raise ValueError(
                "Group audio time must be finite and within playback audio time."
            )
        if (self.ended_at is None) != (self.end_reason is None):
            raise ValueError("Playback end time and reason must be supplied together.")
        if self.ended_at is not None:
            _aware(self.ended_at, "Playback end")
            if self.ended_at < self.started_at:
                raise ValueError("Playback cannot end before it starts.")

    @property
    def active(self) -> bool:
        return self.ended_at is None


@dataclass(frozen=True, slots=True)
class ListenerPresence:
    id: ListenerPresenceId
    session_id: ListeningSessionId
    user_id: UserId
    joined_at: datetime
    confirmed_at: datetime
    deafened: bool
    left_at: datetime | None = None

    def __post_init__(self) -> None:
        _aware(self.joined_at, "Presence join")
        _aware(self.confirmed_at, "Presence confirmation")
        if self.confirmed_at < self.joined_at:
            raise ValueError("Presence confirmation cannot precede its join.")
        if self.left_at is not None:
            _aware(self.left_at, "Presence leave")
            if self.left_at < self.confirmed_at:
                raise ValueError("Presence leave cannot precede its confirmation.")

    @property
    def active(self) -> bool:
        return self.left_at is None


@dataclass(frozen=True, slots=True)
class PlaybackListener:
    playback_id: PlaybackRecordId
    user_id: UserId
    audio_seconds: float
    first_heard_at: datetime
    last_heard_at: datetime

    def __post_init__(self) -> None:
        if not isfinite(self.audio_seconds) or self.audio_seconds < 0:
            raise ValueError("Listener audio time must be finite and non-negative.")
        _aware(self.first_heard_at, "First heard time")
        _aware(self.last_heard_at, "Last heard time")
        if self.last_heard_at < self.first_heard_at:
            raise ValueError("Last heard time cannot precede first heard time.")


@dataclass(frozen=True, slots=True)
class AudienceMember:
    user_id: UserId
    deafened: bool = False


@dataclass(frozen=True, slots=True)
class AudienceState:
    session_id: ListeningSessionId
    human_count: int
    audible_human_count: int
    members: tuple[AudienceMember, ...]
    observed_at: datetime | None

    def __post_init__(self) -> None:
        if (
            self.human_count < 0
            or not 0 <= self.audible_human_count <= self.human_count
        ):
            raise ValueError("Audience human counts must be consistent.")
        if self.human_count < len(self.members):
            raise ValueError("Audience cannot contain more known users than humans.")
        if len({member.user_id for member in self.members}) != len(self.members):
            raise ValueError("Audience users must be unique.")
        if type(self.members) is not tuple:
            raise ValueError("Audience members must be an immutable tuple.")
        if self.observed_at is not None:
            _aware(self.observed_at, "Audience observation")

    @property
    def user_ids(self) -> frozenset[UserId]:
        return frozenset(member.user_id for member in self.members)

    @property
    def audible_user_ids(self) -> frozenset[UserId]:
        return frozenset(
            member.user_id for member in self.members if not member.deafened
        )

    @property
    def empty(self) -> bool | None:
        return None if self.observed_at is None else self.human_count == 0


@dataclass(frozen=True, slots=True)
class PlaybackProgress:
    playback_id: PlaybackRecordId
    audio_seconds: float

    def __post_init__(self) -> None:
        if not isfinite(self.audio_seconds) or self.audio_seconds < 0:
            raise ValueError("Playback progress must be finite and non-negative.")
