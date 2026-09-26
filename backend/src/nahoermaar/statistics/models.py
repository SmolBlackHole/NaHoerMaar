# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed read models shared by statistics queries, services and API views."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from nahoermaar.users.domain import UserId


class StatisticsPeriod(StrEnum):
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    YEAR = "year"
    ALL = "all"


class ActivityGranularity(StrEnum):
    DAY = "day"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class Coverage:
    period: StatisticsPeriod
    granularity: ActivityGranularity
    timezone: str
    started_at: datetime
    ended_at: datetime
    recorded_since: datetime | None
    partial: bool


@dataclass(frozen=True, slots=True)
class RequestTotals:
    manual: int
    radio: int

    @property
    def total(self) -> int:
        return self.manual + self.radio


@dataclass(frozen=True, slots=True)
class PlaybackOutcomes:
    started: int
    completed: int
    skipped: int
    stopped: int
    failed: int

    @property
    def completion_rate(self) -> float | None:
        ended = self.completed + self.skipped + self.stopped + self.failed
        return self.completed / ended if ended else None

    @property
    def skip_rate(self) -> float | None:
        ended = self.completed + self.skipped + self.stopped + self.failed
        return self.skipped / ended if ended else None


@dataclass(frozen=True, slots=True)
class PlaybackBreakdown:
    overall: PlaybackOutcomes
    manual: PlaybackOutcomes
    radio: PlaybackOutcomes


@dataclass(frozen=True, slots=True)
class StatisticsTotals:
    requests: RequestTotals
    playback: PlaybackBreakdown
    listening_seconds: float
    presence_seconds: float
    unique_tracks: int
    unique_artists: int
    average_wait_seconds: float | None


@dataclass(frozen=True, slots=True)
class ActivityBucket:
    started_on: date
    granularity: ActivityGranularity
    plays: int
    listening_seconds: float
    presence_seconds: float


@dataclass(frozen=True, slots=True)
class RankedTrack:
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    plays: int
    listening_seconds: float


@dataclass(frozen=True, slots=True)
class RankedArtist:
    artist_id: UUID
    name: str
    plays: int
    listening_seconds: float


@dataclass(frozen=True, slots=True)
class RankedListener:
    user_id: UserId
    discord_id: str
    display_name: str | None
    discord_username: str | None
    discord_avatar_hash: str | None
    manual_requests: int
    plays: int
    presence_seconds: float
    listening_seconds: float


@dataclass(frozen=True, slots=True)
class StatisticsReport:
    user_id: UserId | None
    coverage: Coverage
    totals: StatisticsTotals
    activity: tuple[ActivityBucket, ...]
    top_tracks: tuple[RankedTrack, ...]
    top_artists: tuple[RankedArtist, ...]
    top_listeners: tuple[RankedListener, ...]
