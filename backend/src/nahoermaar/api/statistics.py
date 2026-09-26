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
    GroupStatisticsReport,
    PersonalStatisticsReport,
    PlaybackOutcomes,
    RankedListener,
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


class RankedListenerView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str | None
    discord_username: str | None
    discord_display_name: str | None
    avatar_url: str
    manual_requests: int
    plays: int
    presence_seconds: float
    listening_seconds: float


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


class PersonalStatisticsView(StatisticsView):
    user_id: UUID


def router(application: Application) -> APIRouter:
    routes = APIRouter(prefix="/api/statistics", tags=["statistics"])

    @routes.get("/overview")
    async def overview(
        request: Request,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_7,
    ) -> GroupStatisticsView:
        authenticated(request)
        return group_statistics_view(
            await application.statistics.overview(period),
            application.avatars,
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
            await application.statistics.user(UserId(user_id), period),
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
    return GroupStatisticsView(
        **common.model_dump(),
        active_listeners=report.active_listeners,
        top_listeners=tuple(
            _ranked_listener_view(item, members_by_id.get(item.discord_id), avatars)
            for item in report.top_listeners
        ),
    )


def personal_statistics_view(
    report: PersonalStatisticsReport,
) -> PersonalStatisticsView:
    common = statistics_view(report)
    return PersonalStatisticsView(
        **common.model_dump(),
        user_id=report.user_id,
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
    return application.gateway.members() if application.gateway else ()


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
        plays=listener.plays,
        presence_seconds=listener.presence_seconds,
        listening_seconds=listener.listening_seconds,
    )
