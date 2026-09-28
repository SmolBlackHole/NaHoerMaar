# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated shared and personal statistics endpoints."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from nahoermaar.bootstrap import Application
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.statistics.models import (
    ActivityGranularity,
    ContagiousTrackHighlight,
    GroupStatisticsReport,
    InfluencedTrack,
    ListenerBadge,
    ListenerBadgeKind,
    ListenerIdentity,
    ListenerPairHighlight,
    PersonalStatisticsReport,
    PlaybackOutcomes,
    RadioConversionHighlight,
    RankedListener,
    SharedTrackHighlight,
    StatisticsPeriod,
    StatisticsReport,
)
from nahoermaar.users.domain import DiscordMember, UserId

from .middleware import authenticated


class CoverageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: StatisticsPeriod
    granularity: ActivityGranularity
    timezone: str
    started_at: datetime
    ended_at: datetime
    recorded_since: datetime | None
    partial: bool


class RequestTotalsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    manual: int
    radio: int


class PlaybackOutcomesView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started: int
    completed: int
    skipped: int
    stopped: int
    failed: int
    completion_rate: float | None
    skip_rate: float | None


class PlaybackBreakdownView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: PlaybackOutcomesView
    manual: PlaybackOutcomesView
    radio: PlaybackOutcomesView


class TotalsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: RequestTotalsView
    playback: PlaybackBreakdownView
    playback_seconds: float
    listening_seconds: float
    presence_seconds: float
    unique_tracks: int
    unique_artists: int
    average_wait_seconds: float | None


class ActivityBucketView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_on: date
    granularity: ActivityGranularity
    plays: int
    playback_seconds: float
    listening_seconds: float
    presence_seconds: float


class RankedTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    plays: int
    listening_seconds: float


class RankedArtistView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist_id: UUID
    name: str
    plays: int
    listening_seconds: float


class WeekdayListeningView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iso_weekday: int
    listening_seconds: float


class HourListeningView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: int
    listening_seconds: float


class ListeningPatternView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekdays: tuple[WeekdayListeningView, ...]
    hours: tuple[HourListeningView, ...]


class PersonalRequestOutcomesView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_requests: int
    played_requests: int
    completed_requests: int
    play_rate: float | None
    completion_rate: float | None


class InfluencedTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    later_requests: int
    distinct_listeners: int


class ListenerBadgeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ListenerBadgeKind
    value: float
    sample_size: int


class RankedListenerView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str | None
    discord_username: str | None
    discord_display_name: str | None
    avatar_url: str
    manual_requests: int
    confirmed_manual_requests: int
    plays: int
    unique_tracks: int
    radio_plays: int
    presence_seconds: float
    listening_seconds: float
    night_listening_seconds: float
    discovery_ratio: float | None
    repeat_ratio: float | None
    radio_share: float | None
    night_share: float | None
    badges: tuple[ListenerBadgeView, ...]


class RankedRequestedTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    requests: int


class RankedRequestedArtistView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist_id: UUID
    name: str
    requests: int


class ListenerIdentityView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str | None
    discord_username: str | None
    discord_display_name: str | None
    avatar_url: str


class SharedTrackHighlightView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    distinct_listeners: int
    plays: int


class ListenerPairHighlightView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first: ListenerIdentityView
    second: ListenerIdentityView
    shared_playbacks: int


class RadioConversionHighlightView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    later_manual_requests: int
    distinct_requesters: int


class ContagiousTrackHighlightView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    original_requester: ListenerIdentityView
    later_manual_requests: int
    distinct_later_requesters: int


class BusiestWeekdayView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iso_weekday: int
    playback_seconds: float


class BusiestHourView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: int
    playback_seconds: float


class ActiveDayStreaksView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: int
    longest: int


class GroupHighlightsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    most_shared_track: SharedTrackHighlightView | None
    listener_pair: ListenerPairHighlightView | None
    radio_conversion: RadioConversionHighlightView | None
    contagious_track: ContagiousTrackHighlightView | None
    busiest_weekday: BusiestWeekdayView | None
    busiest_hour: BusiestHourView | None
    active_day_streaks: ActiveDayStreaksView
    average_listeners: float | None


class PersonalHighlightsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_listening_share: float | None
    listening_pattern: ListeningPatternView
    request_outcomes: PersonalRequestOutcomesView
    radio_discoveries: tuple[RankedTrackView, ...]
    influenced_tracks: tuple[InfluencedTrackView, ...]
    badges: tuple[ListenerBadgeView, ...]


class StatisticsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage: CoverageView
    totals: TotalsView
    activity: tuple[ActivityBucketView, ...]
    top_tracks: tuple[RankedTrackView, ...]
    top_artists: tuple[RankedArtistView, ...]


class GroupStatisticsView(StatisticsView):
    active_listeners: int
    top_listeners: tuple[RankedListenerView, ...]
    requested_tracks: tuple[RankedRequestedTrackView, ...]
    requested_artists: tuple[RankedRequestedArtistView, ...]
    highlights: GroupHighlightsView


class PersonalStatisticsView(StatisticsView):
    user_id: UUID
    top_tracks_by_listening: tuple[RankedTrackView, ...]
    top_artists_by_listening: tuple[RankedArtistView, ...]
    highlights: PersonalHighlightsView


def router(application: Application) -> APIRouter:
    routes = APIRouter(prefix="/api/statistics", tags=["statistics"])

    @routes.get("/overview")
    async def overview(
        request: Request,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_7,
    ) -> GroupStatisticsView:
        authenticated(request)
        return group_statistics_view(
            await application.statistics.service.overview(period),
            application.integrations.avatars,
            _discord_members(application),
        )

    @routes.get("/users/{user_id}")
    async def user_statistics(
        request: Request,
        user_id: UUID,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_30,
    ) -> PersonalStatisticsView:
        authenticated(request)
        return personal_statistics_view(
            await application.statistics.service.user(UserId(user_id), period),
        )

    return routes


def statistics_view(
    report: StatisticsReport,
) -> StatisticsView:
    totals = report.totals
    return StatisticsView(
        coverage=CoverageView(
            period=report.coverage.period,
            granularity=report.coverage.granularity,
            timezone=report.coverage.timezone,
            started_at=report.coverage.started_at,
            ended_at=report.coverage.ended_at,
            recorded_since=report.coverage.recorded_since,
            partial=report.coverage.partial,
        ),
        totals=TotalsView(
            requests=RequestTotalsView(
                total=totals.requests.total,
                manual=totals.requests.manual,
                radio=totals.requests.radio,
            ),
            playback=PlaybackBreakdownView(
                overall=_playback_outcomes_view(totals.playback.overall),
                manual=_playback_outcomes_view(totals.playback.manual),
                radio=_playback_outcomes_view(totals.playback.radio),
            ),
            playback_seconds=totals.playback_seconds,
            listening_seconds=totals.listening_seconds,
            presence_seconds=totals.presence_seconds,
            unique_tracks=totals.unique_tracks,
            unique_artists=totals.unique_artists,
            average_wait_seconds=totals.average_wait_seconds,
        ),
        activity=tuple(
            ActivityBucketView(
                started_on=item.started_on,
                granularity=item.granularity,
                plays=item.plays,
                playback_seconds=item.playback_seconds,
                listening_seconds=item.listening_seconds,
                presence_seconds=item.presence_seconds,
            )
            for item in report.activity
        ),
        top_tracks=tuple(
            RankedTrackView(
                track_id=item.track_id,
                title=item.title,
                artist_names=item.artist_names,
                artwork_url=item.artwork_url,
                plays=item.plays,
                listening_seconds=item.listening_seconds,
            )
            for item in report.top_tracks
        ),
        top_artists=tuple(
            RankedArtistView(
                artist_id=item.artist_id,
                name=item.name,
                plays=item.plays,
                listening_seconds=item.listening_seconds,
            )
            for item in report.top_artists
        ),
    )


def group_statistics_view(
    report: GroupStatisticsReport,
    avatars: DiscordAvatarStore,
    discord_members: tuple[DiscordMember, ...] = (),
) -> GroupStatisticsView:
    members_by_id: dict[str, DiscordMember] = {}
    for member in discord_members:
        members_by_id.setdefault(member.discord_id, member)
    common = statistics_view(report)
    highlights = report.highlights
    return GroupStatisticsView(
        **common.model_dump(),
        active_listeners=report.active_listeners,
        top_listeners=tuple(
            _ranked_listener_view(item, members_by_id.get(item.discord_id), avatars)
            for item in report.top_listeners
        ),
        requested_tracks=tuple(
            RankedRequestedTrackView(
                track_id=item.track_id,
                title=item.title,
                artist_names=item.artist_names,
                artwork_url=item.artwork_url,
                requests=item.requests,
            )
            for item in report.requested_tracks
        ),
        requested_artists=tuple(
            RankedRequestedArtistView(
                artist_id=item.artist_id,
                name=item.name,
                requests=item.requests,
            )
            for item in report.requested_artists
        ),
        highlights=GroupHighlightsView(
            most_shared_track=_shared_track_view(highlights.most_shared_track),
            listener_pair=_listener_pair_view(
                highlights.listener_pair,
                members_by_id,
                avatars,
            ),
            radio_conversion=_radio_conversion_view(highlights.radio_conversion),
            contagious_track=_contagious_track_view(
                highlights.contagious_track,
                members_by_id,
                avatars,
            ),
            busiest_weekday=(
                BusiestWeekdayView(
                    iso_weekday=highlights.busiest_weekday.iso_weekday,
                    playback_seconds=highlights.busiest_weekday.playback_seconds,
                )
                if highlights.busiest_weekday
                else None
            ),
            busiest_hour=(
                BusiestHourView(
                    hour=highlights.busiest_hour.hour,
                    playback_seconds=highlights.busiest_hour.playback_seconds,
                )
                if highlights.busiest_hour
                else None
            ),
            active_day_streaks=ActiveDayStreaksView(
                current=highlights.active_day_streaks.current,
                longest=highlights.active_day_streaks.longest,
            ),
            average_listeners=highlights.average_listeners,
        ),
    )


def personal_statistics_view(
    report: PersonalStatisticsReport,
) -> PersonalStatisticsView:
    common = statistics_view(report)
    return PersonalStatisticsView(
        **common.model_dump(),
        user_id=report.user_id,
        top_tracks_by_listening=tuple(
            RankedTrackView(
                track_id=item.track_id,
                title=item.title,
                artist_names=item.artist_names,
                artwork_url=item.artwork_url,
                plays=item.plays,
                listening_seconds=item.listening_seconds,
            )
            for item in report.top_tracks_by_listening
        ),
        top_artists_by_listening=tuple(
            RankedArtistView(
                artist_id=item.artist_id,
                name=item.name,
                plays=item.plays,
                listening_seconds=item.listening_seconds,
            )
            for item in report.top_artists_by_listening
        ),
        highlights=PersonalHighlightsView(
            group_listening_share=report.highlights.group_listening_share,
            listening_pattern=ListeningPatternView(
                weekdays=tuple(
                    WeekdayListeningView(
                        iso_weekday=item.iso_weekday,
                        listening_seconds=item.listening_seconds,
                    )
                    for item in report.highlights.listening_pattern.weekdays
                ),
                hours=tuple(
                    HourListeningView(
                        hour=item.hour,
                        listening_seconds=item.listening_seconds,
                    )
                    for item in report.highlights.listening_pattern.hours
                ),
            ),
            request_outcomes=PersonalRequestOutcomesView(
                manual_requests=report.highlights.request_outcomes.manual_requests,
                played_requests=report.highlights.request_outcomes.played_requests,
                completed_requests=(
                    report.highlights.request_outcomes.completed_requests
                ),
                play_rate=report.highlights.request_outcomes.play_rate,
                completion_rate=report.highlights.request_outcomes.completion_rate,
            ),
            radio_discoveries=tuple(
                RankedTrackView(
                    track_id=item.track_id,
                    title=item.title,
                    artist_names=item.artist_names,
                    artwork_url=item.artwork_url,
                    plays=item.plays,
                    listening_seconds=item.listening_seconds,
                )
                for item in report.highlights.radio_discoveries
            ),
            influenced_tracks=tuple(
                _influenced_track_view(item)
                for item in report.highlights.influenced_tracks
            ),
            badges=tuple(
                _listener_badge_view(item) for item in report.highlights.badges
            ),
        ),
    )


def _influenced_track_view(track: InfluencedTrack) -> InfluencedTrackView:
    return InfluencedTrackView(
        track_id=track.track_id,
        title=track.title,
        artist_names=track.artist_names,
        artwork_url=track.artwork_url,
        later_requests=track.later_requests,
        distinct_listeners=track.distinct_listeners,
    )


def _playback_outcomes_view(outcomes: PlaybackOutcomes) -> PlaybackOutcomesView:
    return PlaybackOutcomesView(
        started=outcomes.started,
        completed=outcomes.completed,
        skipped=outcomes.skipped,
        stopped=outcomes.stopped,
        failed=outcomes.failed,
        completion_rate=outcomes.completion_rate,
        skip_rate=outcomes.skip_rate,
    )


def _discord_members(application: Application) -> tuple[DiscordMember, ...]:
    gateway = application.integrations.gateway
    return gateway.members() if gateway else ()


def _ranked_listener_view(
    listener: RankedListener,
    member: DiscordMember | None,
    avatars: DiscordAvatarStore,
) -> RankedListenerView:
    return RankedListenerView(
        user_id=listener.user_id,
        display_name=listener.display_name,
        discord_username=listener.discord_username
        or (member.username if member else None),
        discord_display_name=member.display_name if member else None,
        avatar_url=avatars.public_url(
            listener.discord_id,
            avatar_hash=listener.discord_avatar_hash,
            source_url=member.avatar_url if member else None,
        ),
        manual_requests=listener.manual_requests,
        confirmed_manual_requests=listener.confirmed_manual_requests,
        plays=listener.plays,
        unique_tracks=listener.unique_tracks,
        radio_plays=listener.radio_plays,
        presence_seconds=listener.presence_seconds,
        listening_seconds=listener.listening_seconds,
        night_listening_seconds=listener.night_listening_seconds,
        discovery_ratio=listener.discovery_ratio,
        repeat_ratio=listener.repeat_ratio,
        radio_share=listener.radio_share,
        night_share=listener.night_share,
        badges=tuple(_listener_badge_view(item) for item in listener.badges),
    )


def _listener_badge_view(badge: ListenerBadge) -> ListenerBadgeView:
    return ListenerBadgeView(
        kind=badge.kind,
        value=badge.value,
        sample_size=badge.sample_size,
    )


def _listener_identity_view(
    identity: ListenerIdentity,
    member: DiscordMember | None,
    avatars: DiscordAvatarStore,
) -> ListenerIdentityView:
    return ListenerIdentityView(
        user_id=identity.user_id,
        display_name=identity.display_name,
        discord_username=identity.discord_username
        or (member.username if member else None),
        discord_display_name=member.display_name if member else None,
        avatar_url=avatars.public_url(
            identity.discord_id,
            avatar_hash=identity.discord_avatar_hash,
            source_url=member.avatar_url if member else None,
        ),
    )


def _shared_track_view(
    highlight: SharedTrackHighlight | None,
) -> SharedTrackHighlightView | None:
    if highlight is None:
        return None
    return SharedTrackHighlightView(
        track_id=highlight.track_id,
        title=highlight.title,
        artist_names=highlight.artist_names,
        artwork_url=highlight.artwork_url,
        distinct_listeners=highlight.distinct_listeners,
        plays=highlight.plays,
    )


def _listener_pair_view(
    highlight: ListenerPairHighlight | None,
    members_by_id: dict[str, DiscordMember],
    avatars: DiscordAvatarStore,
) -> ListenerPairHighlightView | None:
    if highlight is None:
        return None
    return ListenerPairHighlightView(
        first=_listener_identity_view(
            highlight.first,
            members_by_id.get(highlight.first.discord_id),
            avatars,
        ),
        second=_listener_identity_view(
            highlight.second,
            members_by_id.get(highlight.second.discord_id),
            avatars,
        ),
        shared_playbacks=highlight.shared_playbacks,
    )


def _radio_conversion_view(
    highlight: RadioConversionHighlight | None,
) -> RadioConversionHighlightView | None:
    if highlight is None:
        return None
    return RadioConversionHighlightView(
        track_id=highlight.track_id,
        title=highlight.title,
        artist_names=highlight.artist_names,
        artwork_url=highlight.artwork_url,
        later_manual_requests=highlight.later_manual_requests,
        distinct_requesters=highlight.distinct_requesters,
    )


def _contagious_track_view(
    highlight: ContagiousTrackHighlight | None,
    members_by_id: dict[str, DiscordMember],
    avatars: DiscordAvatarStore,
) -> ContagiousTrackHighlightView | None:
    if highlight is None:
        return None
    return ContagiousTrackHighlightView(
        track_id=highlight.track_id,
        title=highlight.title,
        artist_names=highlight.artist_names,
        artwork_url=highlight.artwork_url,
        original_requester=_listener_identity_view(
            highlight.original_requester,
            members_by_id.get(highlight.original_requester.discord_id),
            avatars,
        ),
        later_manual_requests=highlight.later_manual_requests,
        distinct_later_requesters=highlight.distinct_later_requesters,
    )
