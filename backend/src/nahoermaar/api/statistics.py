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
from nahoermaar.statistics.repository import RankedListener
from nahoermaar.statistics.service import (
    StatisticsPeriod,
    StatisticsReport,
)
from nahoermaar.users.domain import DiscordMember, UserId

from .middleware import authenticated


class CoverageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: StatisticsPeriod
    timezone: str
    started_at: datetime
    ended_at: datetime
    recorded_since: datetime | None
    partial: bool


class TotalsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: int
    manual_requests: int
    radio_requests: int
    plays: int
    completed: int
    skipped: int
    stopped: int
    failed: int
    listening_seconds: float
    unique_tracks: int
    unique_artists: int
    average_wait_seconds: float | None
    completion_rate: float | None
    skip_rate: float | None


class DailyActivityView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date
    plays: int
    listening_seconds: float


class RankedTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
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
    discord_avatar_url: str | None
    pixabot: str | None
    plays: int
    listening_seconds: float


class StatisticsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID | None
    coverage: CoverageView
    totals: TotalsView
    daily_activity: tuple[DailyActivityView, ...]
    top_tracks: tuple[RankedTrackView, ...]
    top_artists: tuple[RankedArtistView, ...]
    top_listeners: tuple[RankedListenerView, ...]


def router(application: Application) -> APIRouter:
    routes = APIRouter(prefix="/api/statistics", tags=["statistics"])

    @routes.get("/overview")
    async def overview(
        request: Request,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_7,
    ) -> StatisticsView:
        authenticated(request)
        return statistics_view(
            await application.statistics.overview(period),
            _discord_members(application),
        )

    @routes.get("/users/{user_id}")
    async def user_statistics(
        request: Request,
        user_id: UUID,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_30,
    ) -> StatisticsView:
        authenticated(request)
        return statistics_view(
            await application.statistics.user(UserId(user_id), period),
            _discord_members(application),
        )

    return routes


def statistics_view(
    report: StatisticsReport,
    discord_members: tuple[DiscordMember, ...] = (),
) -> StatisticsView:
    members_by_id: dict[str, DiscordMember] = {}
    for member in discord_members:
        members_by_id.setdefault(member.discord_id, member)
    totals = report.totals
    return StatisticsView(
        user_id=report.user_id,
        coverage=CoverageView(
            period=report.coverage.period,
            timezone=report.coverage.timezone,
            started_at=report.coverage.started_at,
            ended_at=report.coverage.ended_at,
            recorded_since=report.coverage.recorded_since,
            partial=report.coverage.partial,
        ),
        totals=TotalsView(
            requests=totals.requests,
            manual_requests=totals.manual_requests,
            radio_requests=totals.radio_requests,
            plays=totals.plays,
            completed=totals.completed,
            skipped=totals.skipped,
            stopped=totals.stopped,
            failed=totals.failed,
            listening_seconds=totals.listening_seconds,
            unique_tracks=totals.unique_tracks,
            unique_artists=totals.unique_artists,
            average_wait_seconds=totals.average_wait_seconds,
            completion_rate=report.completion_rate,
            skip_rate=report.skip_rate,
        ),
        daily_activity=tuple(
            DailyActivityView(
                day=item.day,
                plays=item.plays,
                listening_seconds=item.listening_seconds,
            )
            for item in report.daily_activity
        ),
        top_tracks=tuple(
            RankedTrackView(
                track_id=item.track_id,
                title=item.title,
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
        top_listeners=tuple(
            _ranked_listener_view(item, members_by_id.get(item.discord_id))
            for item in report.top_listeners
        ),
    )


def _discord_members(application: Application) -> tuple[DiscordMember, ...]:
    return application.gateway.members() if application.gateway else ()


def _ranked_listener_view(
    listener: RankedListener,
    member: DiscordMember | None,
) -> RankedListenerView:
    return RankedListenerView(
        user_id=listener.user_id,
        display_name=listener.display_name,
        discord_username=listener.discord_username
        or (member.username if member else None),
        discord_display_name=member.display_name if member else None,
        discord_avatar_url=member.avatar_url if member else None,
        pixabot=listener.pixabot,
        plays=listener.plays,
        listening_seconds=listener.listening_seconds,
    )
