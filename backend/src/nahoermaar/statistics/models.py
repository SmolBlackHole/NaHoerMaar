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


class ListenerBadgeKind(StrEnum):
    NIGHT_OWL = "night_owl"
    EXPLORER = "explorer"
    RESIDENT_DJ = "resident_dj"
    RADIO_REGULAR = "radio_regular"
    REPEAT_OFFENDER = "repeat_offender"
    ALWAYS_AROUND = "always_around"
    ALL_EARS = "all_ears"
    QUEUE_CURATOR = "queue_curator"
    RADIO_RIDER = "radio_rider"
    WIDE_ROTATION = "wide_rotation"


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
    playback_seconds: float
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
    playback_seconds: float
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
class RankedRequestedTrack:
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    requests: int


@dataclass(frozen=True, slots=True)
class RankedRequestedArtist:
    artist_id: UUID
    name: str
    requests: int


@dataclass(frozen=True, slots=True)
class ListenerBadge:
    kind: ListenerBadgeKind
    value: float
    sample_size: int


@dataclass(frozen=True, slots=True)
class RankedListener:
    user_id: UserId
    discord_id: str
    display_name: str | None
    discord_username: str | None
    discord_avatar_hash: str | None
    manual_requests: int
    confirmed_manual_requests: int
    plays: int
    unique_tracks: int
    radio_plays: int
    presence_seconds: float
    listening_seconds: float
    night_listening_seconds: float
    badges: tuple[ListenerBadge, ...] = ()

    @property
    def discovery_ratio(self) -> float | None:
        return self.unique_tracks / self.plays if self.plays else None

    @property
    def repeat_ratio(self) -> float | None:
        return 1.0 - self.discovery_ratio if self.discovery_ratio is not None else None

    @property
    def radio_share(self) -> float | None:
        return self.radio_plays / self.plays if self.plays else None

    @property
    def night_share(self) -> float | None:
        if self.listening_seconds <= 0:
            return None
        return self.night_listening_seconds / self.listening_seconds


@dataclass(frozen=True, slots=True)
class ListenerIdentity:
    user_id: UserId
    discord_id: str
    display_name: str | None
    discord_username: str | None
    discord_avatar_hash: str | None


@dataclass(frozen=True, slots=True)
class SharedTrackHighlight:
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    distinct_listeners: int
    plays: int


@dataclass(frozen=True, slots=True)
class ListenerPairHighlight:
    first: ListenerIdentity
    second: ListenerIdentity
    shared_playbacks: int


@dataclass(frozen=True, slots=True)
class RadioConversionHighlight:
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    later_manual_requests: int
    distinct_requesters: int


@dataclass(frozen=True, slots=True)
class ContagiousTrackHighlight:
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    original_requester: ListenerIdentity
    later_manual_requests: int
    distinct_later_requesters: int


@dataclass(frozen=True, slots=True)
class BusiestWeekday:
    iso_weekday: int
    playback_seconds: float


@dataclass(frozen=True, slots=True)
class BusiestHour:
    hour: int
    playback_seconds: float


@dataclass(frozen=True, slots=True)
class ActiveDayStreaks:
    current: int
    longest: int


@dataclass(frozen=True, slots=True)
class GroupHighlights:
    most_shared_track: SharedTrackHighlight | None
    listener_pair: ListenerPairHighlight | None
    radio_conversion: RadioConversionHighlight | None
    contagious_track: ContagiousTrackHighlight | None
    busiest_weekday: BusiestWeekday | None
    busiest_hour: BusiestHour | None
    active_day_streaks: ActiveDayStreaks
    average_listeners: float | None


@dataclass(frozen=True, slots=True)
class StatisticsReport:
    coverage: Coverage
    totals: StatisticsTotals
    activity: tuple[ActivityBucket, ...]
    top_tracks: tuple[RankedTrack, ...]
    top_artists: tuple[RankedArtist, ...]


@dataclass(frozen=True, slots=True)
class GroupStatisticsReport(StatisticsReport):
    active_listeners: int
    top_listeners: tuple[RankedListener, ...]
    requested_tracks: tuple[RankedRequestedTrack, ...]
    requested_artists: tuple[RankedRequestedArtist, ...]
    highlights: GroupHighlights


@dataclass(frozen=True, slots=True)
class PersonalStatisticsReport(StatisticsReport):
    user_id: UserId
