# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed read models shared by statistics queries, services and API views."""

from dataclasses import dataclass, field
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
    LONG_HAUL = "long_haul"
    QUEUE_ARCHITECT = "queue_architect"
    LOCKED_IN = "locked_in"
    DAWN_PATROL = "dawn_patrol"
    WEEKEND_REGULAR = "weekend_regular"
    TASTE_MAKER = "taste_maker"
    RADIO_CONVERT = "radio_convert"
    ARTIST_EXPLORER = "artist_explorer"
    LISTENING_STREAK = "listening_streak"


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
class WeekdayListening:
    iso_weekday: int
    listening_seconds: float


@dataclass(frozen=True, slots=True)
class HourListening:
    hour: int
    listening_seconds: float


@dataclass(frozen=True, slots=True)
class ListeningPattern:
    weekdays: tuple[WeekdayListening, ...]
    hours: tuple[HourListening, ...]


@dataclass(frozen=True, slots=True)
class PersonalRequestOutcomes:
    manual_requests: int
    played_requests: int
    completed_requests: int

    @property
    def play_rate(self) -> float | None:
        if self.manual_requests == 0:
            return None
        return self.played_requests / self.manual_requests

    @property
    def completion_rate(self) -> float | None:
        if self.played_requests == 0:
            return None
        return self.completed_requests / self.played_requests


@dataclass(frozen=True, slots=True)
class InfluencedTrack:
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    later_requests: int
    distinct_listeners: int


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
class RankedLibraryTrack:
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    count: int


@dataclass(frozen=True, slots=True)
class LibraryStatistics:
    likes: int
    dislikes: int
    public_playlists: int
    shared_playlists: int
    top_liked_tracks: tuple[RankedLibraryTrack, ...]
    top_disliked_tracks: tuple[RankedLibraryTrack, ...]
    most_saved_tracks: tuple[RankedLibraryTrack, ...]

    @property
    def reactions(self) -> int:
        return self.likes + self.dislikes

    @property
    def like_share(self) -> float | None:
        return self.likes / self.reactions if self.reactions else None


@dataclass(frozen=True, slots=True)
class ListenerBadge:
    kind: ListenerBadgeKind
    value: float
    sample_size: int


@dataclass(frozen=True, slots=True)
class ListenerAchievementFacts:
    dawn_listening_seconds: float = 0.0
    weekend_listening_seconds: float = 0.0
    distinct_artists: int = 0
    influenced_tracks: int = 0
    radio_converted_tracks: int = 0
    active_listening_days: int = 0
    longest_listening_streak: int = 0


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
    achievement_facts: ListenerAchievementFacts = field(
        default_factory=ListenerAchievementFacts
    )
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
class PersonalHighlights:
    group_listening_share: float | None
    listening_pattern: ListeningPattern
    request_outcomes: PersonalRequestOutcomes
    radio_discoveries: tuple[RankedTrack, ...]
    influenced_tracks: tuple[InfluencedTrack, ...]
    badges: tuple[ListenerBadge, ...]


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
    library: LibraryStatistics


@dataclass(frozen=True, slots=True)
class PersonalStatisticsReport(StatisticsReport):
    user_id: UserId
    top_tracks_by_listening: tuple[RankedTrack, ...]
    top_artists_by_listening: tuple[RankedArtist, ...]
    highlights: PersonalHighlights
