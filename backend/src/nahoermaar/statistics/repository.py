# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Direct PostgreSQL projections over requests, plays and listener facts."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from sqlalchemy import (
    Date,
    Table,
    and_,
    case,
    cast as sql_cast,
    func,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, FromClause
from sqlalchemy.sql.selectable import ScalarSelect, Subquery

from nahoermaar.database.schema import Base
from nahoermaar.users.domain import UserId

from .models import (
    ActivityBucket,
    ActivityGranularity,
    BusiestHour,
    BusiestWeekday,
    ContagiousTrackHighlight,
    HourListening,
    InfluencedTrack,
    ListenerIdentity,
    ListeningPattern,
    ListenerPairHighlight,
    PersonalRequestOutcomes,
    PlaybackBreakdown,
    PlaybackOutcomes,
    RadioConversionHighlight,
    RankedArtist,
    RankedListener,
    RankedRequestedArtist,
    RankedRequestedTrack,
    RankedTrack,
    RequestTotals,
    SharedTrackHighlight,
    StatisticsTotals,
    WeekdayListening,
)


@dataclass(frozen=True, slots=True)
class PresenceInterval:
    started_at: datetime
    ended_at: datetime


class StatisticsRepository:
    """Execute read-only aggregate queries without hydrating domain aggregates."""

    __slots__ = (
        "_artists",
        "_discord",
        "_listeners",
        "_playbacks",
        "_presence",
        "_profiles",
        "_requests",
        "_session",
        "_track_artists",
        "_tracks",
        "_users",
    )

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._requests = _table("track_requests")
        self._playbacks = _table("playback_records")
        self._listeners = _table("playback_listeners")
        self._presence = _table("listener_presence")
        self._tracks = _table("tracks")
        self._track_artists = _table("track_artists")
        self._artists = _table("artists")
        self._users = _table("users")
        self._profiles = _table("user_profiles")
        self._discord = _table("discord_identities")

    async def recorded_since(self) -> datetime | None:
        events = union_all(
            select(self._requests.c.requested_at.label("occurred_at")),
            select(self._playbacks.c.started_at.label("occurred_at")),
            select(self._presence.c.joined_at.label("occurred_at")),
        ).subquery()
        value = await self._session.scalar(select(func.min(events.c.occurred_at)))
        return cast(datetime | None, value)

    async def totals(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None = None,
    ) -> StatisticsTotals:
        requests = await self._request_totals(started_at, ended_at, user_id=user_id)
        playback = await self._playback_breakdown(
            started_at,
            ended_at,
            user_id=user_id,
        )
        (
            playback_seconds,
            listening_seconds,
            unique_tracks,
            unique_artists,
        ) = await self._listening_totals(started_at, ended_at, user_id=user_id)
        presence = sum(
            (interval.ended_at - interval.started_at).total_seconds()
            for interval in await self.presence_intervals(
                started_at,
                ended_at,
                user_id=user_id,
            )
        )
        average_wait = await self._average_manual_wait(
            started_at,
            ended_at,
            user_id=user_id,
        )
        return StatisticsTotals(
            requests=requests,
            playback=playback,
            playback_seconds=playback_seconds,
            listening_seconds=listening_seconds,
            presence_seconds=presence,
            unique_tracks=unique_tracks,
            unique_artists=unique_artists,
            average_wait_seconds=average_wait,
        )

    async def activity(
        self,
        started_at: datetime,
        ended_at: datetime,
        timezone: str,
        granularity: ActivityGranularity,
        *,
        user_id: UserId | None = None,
    ) -> tuple[ActivityBucket, ...]:
        relation, conditions, audio_seconds = self._listening_scope(
            started_at,
            ended_at,
            user_id,
        )
        bucket = sql_cast(
            func.date_trunc(
                granularity.value,
                func.timezone(timezone, self._playbacks.c.started_at),
            ),
            Date,
        ).label("started_on")
        plays = func.count(func.distinct(self._playbacks.c.id)).label("plays")
        listening = func.coalesce(func.sum(audio_seconds), 0.0).label(
            "listening_seconds"
        )
        playback = func.coalesce(
            func.sum(self._playbacks.c.group_audio_seconds), 0.0
        ).label("playback_seconds")
        rows = (
            (
                await self._session.execute(
                    select(bucket, plays, playback, listening)
                    .select_from(relation)
                    .where(and_(*conditions))
                    .group_by(bucket)
                    .order_by(bucket)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            ActivityBucket(
                started_on=row["started_on"],
                granularity=granularity,
                plays=int(row["plays"]),
                playback_seconds=float(row["playback_seconds"]),
                listening_seconds=float(row["listening_seconds"]),
                presence_seconds=0.0,
            )
            for row in rows
        )

    async def presence_intervals(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None = None,
    ) -> tuple[PresenceInterval, ...]:
        known_end = func.coalesce(
            self._presence.c.left_at, self._presence.c.confirmed_at
        )
        conditions = [
            self._presence.c.joined_at < ended_at,
            known_end > started_at,
        ]
        if user_id is not None:
            conditions.append(self._presence.c.user_id == user_id)
        rows = (
            (
                await self._session.execute(
                    select(
                        func.greatest(self._presence.c.joined_at, started_at).label(
                            "started_at"
                        ),
                        func.least(known_end, ended_at).label("ended_at"),
                    )
                    .where(and_(*conditions))
                    .order_by(self._presence.c.joined_at, self._presence.c.id)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            PresenceInterval(row["started_at"], row["ended_at"]) for row in rows
        )

    async def top_tracks(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None = None,
        limit: int = 5,
        sort_by_listening: bool = False,
        origin: str | None = None,
    ) -> tuple[RankedTrack, ...]:
        relation, conditions, audio_seconds = self._listening_scope(
            started_at,
            ended_at,
            user_id,
        )
        relation = relation.join(
            self._tracks,
            self._tracks.c.id == self._requests.c.track_id,
        )
        plays = func.count(func.distinct(self._playbacks.c.id)).label("plays")
        listening = func.coalesce(func.sum(audio_seconds), 0.0).label(
            "listening_seconds"
        )
        if origin is not None:
            conditions.append(self._requests.c.origin == origin)
        ordering = (
            (listening.desc(), plays.desc())
            if sort_by_listening
            else (plays.desc(), listening.desc())
        )
        artists = (
            select(
                func.array_agg(
                    aggregate_order_by(
                        self._artists.c.name,
                        self._track_artists.c.position,
                    )
                )
            )
            .select_from(
                self._track_artists.join(
                    self._artists,
                    self._artists.c.id == self._track_artists.c.artist_id,
                )
            )
            .where(self._track_artists.c.track_id == self._tracks.c.id)
            .scalar_subquery()
        )
        rows = (
            (
                await self._session.execute(
                    select(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        artists.label("artist_names"),
                        plays,
                        listening,
                    )
                    .select_from(relation)
                    .where(and_(*conditions))
                    .group_by(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                    )
                    .order_by(*ordering, self._tracks.c.id)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            RankedTrack(
                track_id=row["id"],
                title=row["title"],
                artist_names=tuple(cast(list[str] | None, row["artist_names"]) or ()),
                artwork_url=row["artwork_url"],
                plays=int(row["plays"]),
                listening_seconds=float(row["listening_seconds"]),
            )
            for row in rows
        )

    async def top_artists(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None = None,
        limit: int = 5,
        sort_by_listening: bool = False,
    ) -> tuple[RankedArtist, ...]:
        relation, conditions, audio_seconds = self._listening_scope(
            started_at,
            ended_at,
            user_id,
        )
        relation = relation.join(
            self._track_artists,
            self._track_artists.c.track_id == self._requests.c.track_id,
        ).join(
            self._artists,
            self._artists.c.id == self._track_artists.c.artist_id,
        )
        plays = func.count(func.distinct(self._playbacks.c.id)).label("plays")
        listening = func.coalesce(func.sum(audio_seconds), 0.0).label(
            "listening_seconds"
        )
        ordering = (
            (listening.desc(), plays.desc())
            if sort_by_listening
            else (plays.desc(), listening.desc())
        )
        rows = (
            (
                await self._session.execute(
                    select(
                        self._artists.c.id,
                        self._artists.c.name,
                        plays,
                        listening,
                    )
                    .select_from(relation)
                    .where(and_(*conditions))
                    .group_by(self._artists.c.id, self._artists.c.name)
                    .order_by(*ordering, self._artists.c.id)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            RankedArtist(
                artist_id=row["id"],
                name=row["name"],
                plays=int(row["plays"]),
                listening_seconds=float(row["listening_seconds"]),
            )
            for row in rows
        )

    async def listening_pattern(
        self,
        started_at: datetime,
        ended_at: datetime,
        timezone: str,
        user_id: UserId,
    ) -> ListeningPattern:
        local_heard = func.timezone(timezone, self._listeners.c.first_heard_at)
        weekday = func.extract("isodow", local_heard).label("iso_weekday")
        hour = func.extract("hour", local_heard).label("hour")
        listening = func.sum(self._listeners.c.audio_seconds).label("listening_seconds")
        relation = self._listeners.join(
            self._playbacks,
            self._playbacks.c.id == self._listeners.c.playback_id,
        )
        conditions = (
            self._listeners.c.user_id == user_id,
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        )
        weekday_rows = (
            (
                await self._session.execute(
                    select(weekday, listening)
                    .select_from(relation)
                    .where(*conditions)
                    .group_by(weekday)
                    .order_by(weekday)
                )
            )
            .mappings()
            .all()
        )
        hour_rows = (
            (
                await self._session.execute(
                    select(hour, listening)
                    .select_from(relation)
                    .where(*conditions)
                    .group_by(hour)
                    .order_by(hour)
                )
            )
            .mappings()
            .all()
        )
        weekday_seconds = {
            int(row["iso_weekday"]): float(row["listening_seconds"] or 0.0)
            for row in weekday_rows
        }
        hour_seconds = {
            int(row["hour"]): float(row["listening_seconds"] or 0.0)
            for row in hour_rows
        }
        return ListeningPattern(
            weekdays=tuple(
                WeekdayListening(day, weekday_seconds.get(day, 0.0))
                for day in range(1, 8)
            ),
            hours=tuple(
                HourListening(hour_value, hour_seconds.get(hour_value, 0.0))
                for hour_value in range(24)
            ),
        )

    async def group_listening_seconds(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> float:
        value = await self._session.scalar(
            select(func.coalesce(func.sum(self._listeners.c.audio_seconds), 0.0))
            .select_from(
                self._listeners.join(
                    self._playbacks,
                    self._playbacks.c.id == self._listeners.c.playback_id,
                )
            )
            .where(
                self._playbacks.c.started_at >= started_at,
                self._playbacks.c.started_at < ended_at,
            )
        )
        return float(value or 0.0)

    async def personal_request_outcomes(
        self,
        started_at: datetime,
        ended_at: datetime,
        user_id: UserId,
    ) -> PersonalRequestOutcomes:
        relation = self._requests.outerjoin(
            self._playbacks,
            and_(
                self._playbacks.c.request_id == self._requests.c.id,
                self._playbacks.c.started_at < ended_at,
            ),
        )
        row = (
            (
                await self._session.execute(
                    select(
                        func.count(func.distinct(self._requests.c.id)).label(
                            "manual_requests"
                        ),
                        func.count(func.distinct(self._requests.c.id))
                        .filter(self._playbacks.c.id.is_not(None))
                        .label("played_requests"),
                        func.count(func.distinct(self._requests.c.id))
                        .filter(self._playbacks.c.end_reason == "completed")
                        .label("completed_requests"),
                    )
                    .select_from(relation)
                    .where(
                        self._requests.c.requested_by == user_id,
                        self._requests.c.origin == "manual",
                        self._requests.c.requested_at >= started_at,
                        self._requests.c.requested_at < ended_at,
                    )
                )
            )
            .mappings()
            .one()
        )
        return PersonalRequestOutcomes(
            manual_requests=int(row["manual_requests"] or 0),
            played_requests=int(row["played_requests"] or 0),
            completed_requests=int(row["completed_requests"] or 0),
        )

    async def influenced_tracks(
        self,
        started_at: datetime,
        ended_at: datetime,
        user_id: UserId,
        *,
        limit: int = 3,
    ) -> tuple[InfluencedTrack, ...]:
        original = (
            select(
                self._requests.c.track_id,
                func.min(self._requests.c.requested_at).label("requested_at"),
            )
            .where(
                self._requests.c.requested_by == user_id,
                self._requests.c.origin == "manual",
                self._requests.c.requested_at >= started_at,
                self._requests.c.requested_at < ended_at,
            )
            .group_by(self._requests.c.track_id)
            .subquery()
        )
        later = self._requests.alias("personal_later_request")
        later_count = func.count().label("later_requests")
        listener_count = func.count(func.distinct(later.c.requested_by)).label(
            "distinct_listeners"
        )
        artists = self._artist_names()
        rows = (
            (
                await self._session.execute(
                    select(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        artists.label("artist_names"),
                        later_count,
                        listener_count,
                    )
                    .select_from(
                        original.join(
                            later,
                            and_(
                                later.c.track_id == original.c.track_id,
                                later.c.origin == "manual",
                                later.c.requested_by != user_id,
                                later.c.requested_at > original.c.requested_at,
                                later.c.requested_at < ended_at,
                            ),
                        ).join(
                            self._tracks,
                            self._tracks.c.id == original.c.track_id,
                        )
                    )
                    .group_by(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                    )
                    .order_by(
                        listener_count.desc(),
                        later_count.desc(),
                        self._tracks.c.id,
                    )
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            InfluencedTrack(
                track_id=row["id"],
                title=row["title"],
                artist_names=tuple(cast(list[str] | None, row["artist_names"]) or ()),
                artwork_url=row["artwork_url"],
                later_requests=int(row["later_requests"]),
                distinct_listeners=int(row["distinct_listeners"]),
            )
            for row in rows
        )

    async def top_requested_tracks(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        limit: int = 5,
    ) -> tuple[RankedRequestedTrack, ...]:
        request_count = func.count().label("requests")
        artists = self._artist_names()
        rows = (
            (
                await self._session.execute(
                    select(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        artists.label("artist_names"),
                        request_count,
                    )
                    .select_from(
                        self._requests.join(
                            self._tracks,
                            self._tracks.c.id == self._requests.c.track_id,
                        )
                    )
                    .where(
                        self._requests.c.requested_at >= started_at,
                        self._requests.c.requested_at < ended_at,
                    )
                    .group_by(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                    )
                    .order_by(request_count.desc(), self._tracks.c.id)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            RankedRequestedTrack(
                track_id=row["id"],
                title=row["title"],
                artist_names=tuple(cast(list[str] | None, row["artist_names"]) or ()),
                artwork_url=row["artwork_url"],
                requests=int(row["requests"]),
            )
            for row in rows
        )

    async def top_requested_artists(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        limit: int = 5,
    ) -> tuple[RankedRequestedArtist, ...]:
        request_count = func.count(func.distinct(self._requests.c.id)).label("requests")
        rows = (
            (
                await self._session.execute(
                    select(
                        self._artists.c.id,
                        self._artists.c.name,
                        request_count,
                    )
                    .select_from(
                        self._requests.join(
                            self._track_artists,
                            self._track_artists.c.track_id == self._requests.c.track_id,
                        ).join(
                            self._artists,
                            self._artists.c.id == self._track_artists.c.artist_id,
                        )
                    )
                    .where(
                        self._requests.c.requested_at >= started_at,
                        self._requests.c.requested_at < ended_at,
                    )
                    .group_by(self._artists.c.id, self._artists.c.name)
                    .order_by(request_count.desc(), self._artists.c.id)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            RankedRequestedArtist(
                artist_id=row["id"],
                name=row["name"],
                requests=int(row["requests"]),
            )
            for row in rows
        )

    async def most_shared_track(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> SharedTrackHighlight | None:
        listener_count = func.count(func.distinct(self._listeners.c.user_id)).label(
            "distinct_listeners"
        )
        play_count = func.count(func.distinct(self._playbacks.c.id)).label("plays")
        artists = self._artist_names()
        row = (
            (
                await self._session.execute(
                    select(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        artists.label("artist_names"),
                        listener_count,
                        play_count,
                    )
                    .select_from(
                        self._playbacks.join(
                            self._requests,
                            self._requests.c.id == self._playbacks.c.request_id,
                        )
                        .join(
                            self._listeners,
                            self._listeners.c.playback_id == self._playbacks.c.id,
                        )
                        .join(
                            self._tracks,
                            self._tracks.c.id == self._requests.c.track_id,
                        )
                    )
                    .where(
                        self._playbacks.c.started_at >= started_at,
                        self._playbacks.c.started_at < ended_at,
                    )
                    .group_by(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                    )
                    .having(listener_count > 1)
                    .order_by(
                        listener_count.desc(),
                        play_count.desc(),
                        self._tracks.c.id,
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return SharedTrackHighlight(
            track_id=row["id"],
            title=row["title"],
            artist_names=tuple(cast(list[str] | None, row["artist_names"]) or ()),
            artwork_url=row["artwork_url"],
            distinct_listeners=int(row["distinct_listeners"]),
            plays=int(row["plays"]),
        )

    async def best_listener_pair(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> ListenerPairHighlight | None:
        first_listener = self._listeners.alias("first_listener")
        second_listener = self._listeners.alias("second_listener")
        first_users = self._users.alias("first_users")
        second_users = self._users.alias("second_users")
        first_profiles = self._profiles.alias("first_profiles")
        second_profiles = self._profiles.alias("second_profiles")
        first_discord = self._discord.alias("first_discord")
        second_discord = self._discord.alias("second_discord")
        shared = func.count(func.distinct(self._playbacks.c.id)).label(
            "shared_playbacks"
        )
        relation = (
            self._playbacks.join(
                first_listener,
                first_listener.c.playback_id == self._playbacks.c.id,
            )
            .join(
                second_listener,
                and_(
                    second_listener.c.playback_id == self._playbacks.c.id,
                    first_listener.c.user_id < second_listener.c.user_id,
                ),
            )
            .join(first_users, first_users.c.id == first_listener.c.user_id)
            .join(second_users, second_users.c.id == second_listener.c.user_id)
            .join(first_profiles, first_profiles.c.user_id == first_users.c.id)
            .join(second_profiles, second_profiles.c.user_id == second_users.c.id)
            .join(first_discord, first_discord.c.user_id == first_users.c.id)
            .join(second_discord, second_discord.c.user_id == second_users.c.id)
        )
        identity_columns = (
            first_users.c.id.label("first_user_id"),
            first_discord.c.discord_id.label("first_discord_id"),
            first_profiles.c.display_name.label("first_display_name"),
            first_discord.c.username.label("first_discord_username"),
            first_discord.c.avatar_hash.label("first_discord_avatar_hash"),
            second_users.c.id.label("second_user_id"),
            second_discord.c.discord_id.label("second_discord_id"),
            second_profiles.c.display_name.label("second_display_name"),
            second_discord.c.username.label("second_discord_username"),
            second_discord.c.avatar_hash.label("second_discord_avatar_hash"),
        )
        row = (
            (
                await self._session.execute(
                    select(*identity_columns, shared)
                    .select_from(relation)
                    .where(
                        self._playbacks.c.started_at >= started_at,
                        self._playbacks.c.started_at < ended_at,
                    )
                    .group_by(*identity_columns)
                    .order_by(
                        shared.desc(),
                        first_users.c.id,
                        second_users.c.id,
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ListenerPairHighlight(
            first=self._listener_identity(row, "first"),
            second=self._listener_identity(row, "second"),
            shared_playbacks=int(row["shared_playbacks"]),
        )

    async def best_radio_conversion(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> RadioConversionHighlight | None:
        radio_starts = (
            select(
                self._requests.c.track_id,
                func.min(self._playbacks.c.started_at).label("first_radio_playback"),
            )
            .select_from(
                self._playbacks.join(
                    self._requests,
                    self._requests.c.id == self._playbacks.c.request_id,
                )
            )
            .where(
                self._playbacks.c.started_at >= started_at,
                self._playbacks.c.started_at < ended_at,
                self._requests.c.origin == "radio",
            )
            .group_by(self._requests.c.track_id)
            .subquery()
        )
        manual = self._requests.alias("later_manual_request")
        manual_count = func.count().label("later_manual_requests")
        requester_count = func.count(func.distinct(manual.c.requested_by)).label(
            "distinct_requesters"
        )
        artists = self._artist_names()
        row = (
            (
                await self._session.execute(
                    select(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        artists.label("artist_names"),
                        manual_count,
                        requester_count,
                    )
                    .select_from(
                        radio_starts.join(
                            manual,
                            and_(
                                manual.c.track_id == radio_starts.c.track_id,
                                manual.c.origin == "manual",
                                manual.c.requested_at
                                > radio_starts.c.first_radio_playback,
                                manual.c.requested_at < ended_at,
                            ),
                        ).join(
                            self._tracks,
                            self._tracks.c.id == radio_starts.c.track_id,
                        )
                    )
                    .group_by(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                    )
                    .order_by(
                        requester_count.desc(),
                        manual_count.desc(),
                        self._tracks.c.id,
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return RadioConversionHighlight(
            track_id=row["id"],
            title=row["title"],
            artist_names=tuple(cast(list[str] | None, row["artist_names"]) or ()),
            artwork_url=row["artwork_url"],
            later_manual_requests=int(row["later_manual_requests"]),
            distinct_requesters=int(row["distinct_requesters"]),
        )

    async def most_contagious_track(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> ContagiousTrackHighlight | None:
        original = (
            select(
                self._requests.c.track_id,
                self._requests.c.requested_by.label("original_user_id"),
                self._requests.c.requested_at.label("original_requested_at"),
            )
            .where(
                self._requests.c.requested_at >= started_at,
                self._requests.c.requested_at < ended_at,
                self._requests.c.origin == "manual",
            )
            .distinct(self._requests.c.track_id)
            .order_by(
                self._requests.c.track_id,
                self._requests.c.requested_at,
                self._requests.c.id,
            )
            .subquery()
        )
        later = self._requests.alias("contagious_later_request")
        users = self._users.alias("original_users")
        profiles = self._profiles.alias("original_profiles")
        discord = self._discord.alias("original_discord")
        later_count = func.count().label("later_manual_requests")
        requester_count = func.count(func.distinct(later.c.requested_by)).label(
            "distinct_later_requesters"
        )
        artists = self._artist_names()
        identity_columns = (
            users.c.id.label("original_user_id"),
            discord.c.discord_id.label("original_discord_id"),
            profiles.c.display_name.label("original_display_name"),
            discord.c.username.label("original_discord_username"),
            discord.c.avatar_hash.label("original_discord_avatar_hash"),
        )
        row = (
            (
                await self._session.execute(
                    select(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        artists.label("artist_names"),
                        *identity_columns,
                        later_count,
                        requester_count,
                    )
                    .select_from(
                        original.join(
                            later,
                            and_(
                                later.c.track_id == original.c.track_id,
                                later.c.origin == "manual",
                                later.c.requested_at > original.c.original_requested_at,
                                later.c.requested_at < ended_at,
                                later.c.requested_by != original.c.original_user_id,
                            ),
                        )
                        .join(
                            self._tracks,
                            self._tracks.c.id == original.c.track_id,
                        )
                        .join(users, users.c.id == original.c.original_user_id)
                        .join(profiles, profiles.c.user_id == users.c.id)
                        .join(discord, discord.c.user_id == users.c.id)
                    )
                    .group_by(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        *identity_columns,
                    )
                    .order_by(
                        requester_count.desc(),
                        later_count.desc(),
                        self._tracks.c.id,
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ContagiousTrackHighlight(
            track_id=row["id"],
            title=row["title"],
            artist_names=tuple(cast(list[str] | None, row["artist_names"]) or ()),
            artwork_url=row["artwork_url"],
            original_requester=self._listener_identity(row, "original"),
            later_manual_requests=int(row["later_manual_requests"]),
            distinct_later_requesters=int(row["distinct_later_requesters"]),
        )

    async def busiest_times(
        self,
        started_at: datetime,
        ended_at: datetime,
        timezone: str,
    ) -> tuple[BusiestWeekday | None, BusiestHour | None]:
        local_start = func.timezone(timezone, self._playbacks.c.started_at)
        weekday = func.extract("isodow", local_start).label("iso_weekday")
        hour = func.extract("hour", local_start).label("hour")
        playback = func.sum(self._playbacks.c.group_audio_seconds).label(
            "playback_seconds"
        )
        conditions = (
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
            self._playbacks.c.group_audio_seconds > 0,
        )
        weekday_row = (
            (
                await self._session.execute(
                    select(weekday, playback)
                    .where(*conditions)
                    .group_by(weekday)
                    .order_by(playback.desc(), weekday)
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        hour_row = (
            (
                await self._session.execute(
                    select(hour, playback)
                    .where(*conditions)
                    .group_by(hour)
                    .order_by(playback.desc(), hour)
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            BusiestWeekday(
                int(weekday_row["iso_weekday"]),
                float(weekday_row["playback_seconds"]),
            )
            if weekday_row
            else None,
            BusiestHour(
                int(hour_row["hour"]),
                float(hour_row["playback_seconds"]),
            )
            if hour_row
            else None,
        )

    async def active_days(
        self,
        started_at: datetime,
        ended_at: datetime,
        timezone: str,
    ) -> tuple[date, ...]:
        active_day = sql_cast(
            func.timezone(timezone, self._playbacks.c.started_at), Date
        ).label("active_day")
        values = (
            await self._session.scalars(
                select(active_day)
                .where(
                    self._playbacks.c.started_at >= started_at,
                    self._playbacks.c.started_at < ended_at,
                    self._playbacks.c.group_audio_seconds > 0,
                )
                .distinct()
                .order_by(active_day)
            )
        ).all()
        return tuple(values)

    async def top_listeners(
        self,
        started_at: datetime,
        ended_at: datetime,
        timezone: str,
        *,
        limit: int | None = None,
    ) -> tuple[RankedListener, ...]:
        manual_requests = (
            select(
                self._requests.c.requested_by.label("user_id"),
                func.count().label("manual_requests"),
            )
            .where(
                self._requests.c.requested_at >= started_at,
                self._requests.c.requested_at < ended_at,
                self._requests.c.origin == "manual",
            )
            .group_by(self._requests.c.requested_by)
            .subquery()
        )
        confirmed_manual_requests = (
            select(
                self._requests.c.requested_by.label("user_id"),
                func.count(func.distinct(self._requests.c.id)).label(
                    "confirmed_manual_requests"
                ),
            )
            .join(
                self._playbacks,
                self._playbacks.c.request_id == self._requests.c.id,
            )
            .where(
                self._playbacks.c.started_at >= started_at,
                self._playbacks.c.started_at < ended_at,
                self._requests.c.origin == "manual",
            )
            .group_by(self._requests.c.requested_by)
            .subquery()
        )
        local_heard = func.timezone(timezone, self._listeners.c.first_heard_at)
        local_heard_end = func.timezone(timezone, self._listeners.c.last_heard_at)
        local_hour = func.extract("hour", local_heard)
        local_day = func.date_trunc("day", local_heard)
        night_start = case(
            (local_hour < 5, local_day),
            else_=local_day + func.make_interval(0, 0, 0, 1),
        )
        night_end = night_start + func.make_interval(0, 0, 0, 0, 5)
        heard_interval_seconds = func.extract("epoch", local_heard_end - local_heard)
        night_overlap_seconds = func.greatest(
            0.0,
            func.extract(
                "epoch",
                func.least(local_heard_end, night_end)
                - func.greatest(local_heard, night_start),
            ),
        )
        night_audio_seconds = case(
            (
                heard_interval_seconds > 0,
                self._listeners.c.audio_seconds
                * night_overlap_seconds
                / heard_interval_seconds,
            ),
            (local_hour < 5, self._listeners.c.audio_seconds),
            else_=0.0,
        )
        listening = (
            select(
                self._listeners.c.user_id,
                func.count(func.distinct(self._listeners.c.playback_id)).label("plays"),
                func.count(func.distinct(self._requests.c.track_id)).label(
                    "unique_tracks"
                ),
                func.count(func.distinct(self._listeners.c.playback_id))
                .filter(self._requests.c.origin == "radio")
                .label("radio_plays"),
                func.sum(self._listeners.c.audio_seconds).label("listening_seconds"),
                func.sum(night_audio_seconds).label("night_listening_seconds"),
            )
            .join(
                self._playbacks,
                self._playbacks.c.id == self._listeners.c.playback_id,
            )
            .join(
                self._requests,
                self._requests.c.id == self._playbacks.c.request_id,
            )
            .where(
                self._playbacks.c.started_at >= started_at,
                self._playbacks.c.started_at < ended_at,
            )
            .group_by(self._listeners.c.user_id)
            .subquery()
        )
        known_end = func.coalesce(
            self._presence.c.left_at, self._presence.c.confirmed_at
        )
        presence = (
            select(
                self._presence.c.user_id,
                func.sum(
                    func.extract(
                        "epoch",
                        func.least(known_end, ended_at)
                        - func.greatest(self._presence.c.joined_at, started_at),
                    )
                ).label("presence_seconds"),
            )
            .where(
                self._presence.c.joined_at < ended_at,
                known_end > started_at,
            )
            .group_by(self._presence.c.user_id)
            .subquery()
        )
        relation = (
            self._users.join(
                self._profiles,
                self._profiles.c.user_id == self._users.c.id,
            )
            .join(
                self._discord,
                self._discord.c.user_id == self._users.c.id,
            )
            .outerjoin(manual_requests, manual_requests.c.user_id == self._users.c.id)
            .outerjoin(
                confirmed_manual_requests,
                confirmed_manual_requests.c.user_id == self._users.c.id,
            )
            .outerjoin(listening, listening.c.user_id == self._users.c.id)
            .outerjoin(presence, presence.c.user_id == self._users.c.id)
        )
        request_count = func.coalesce(manual_requests.c.manual_requests, 0)
        confirmed_request_count = func.coalesce(
            confirmed_manual_requests.c.confirmed_manual_requests, 0
        )
        play_count = func.coalesce(listening.c.plays, 0)
        unique_tracks = func.coalesce(listening.c.unique_tracks, 0)
        radio_plays = func.coalesce(listening.c.radio_plays, 0)
        heard = func.coalesce(listening.c.listening_seconds, 0.0)
        night_heard = func.coalesce(listening.c.night_listening_seconds, 0.0)
        present = func.coalesce(presence.c.presence_seconds, 0.0)
        statement = (
            select(
                self._users.c.id,
                self._discord.c.discord_id,
                self._profiles.c.display_name,
                self._discord.c.username,
                self._discord.c.avatar_hash,
                request_count.label("manual_requests"),
                confirmed_request_count.label("confirmed_manual_requests"),
                play_count.label("plays"),
                unique_tracks.label("unique_tracks"),
                radio_plays.label("radio_plays"),
                present.label("presence_seconds"),
                heard.label("listening_seconds"),
                night_heard.label("night_listening_seconds"),
            )
            .select_from(relation)
            .where(
                self._users.c.role.is_not(None),
                or_(
                    request_count > 0,
                    play_count > 0,
                    present > 0,
                    heard > 0,
                ),
            )
            .order_by(
                heard.desc(),
                play_count.desc(),
                request_count.desc(),
                self._users.c.id,
            )
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(
            RankedListener(
                user_id=UserId(row["id"]),
                discord_id=row["discord_id"],
                display_name=row["display_name"],
                discord_username=row["username"],
                discord_avatar_hash=row["avatar_hash"],
                manual_requests=int(row["manual_requests"]),
                confirmed_manual_requests=int(row["confirmed_manual_requests"]),
                plays=int(row["plays"]),
                unique_tracks=int(row["unique_tracks"]),
                radio_plays=int(row["radio_plays"]),
                presence_seconds=float(row["presence_seconds"]),
                listening_seconds=float(row["listening_seconds"]),
                night_listening_seconds=float(row["night_listening_seconds"]),
            )
            for row in rows
        )

    async def active_listener_count(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> int:
        """Count people with any request, heard playback or channel presence."""
        known_end = func.coalesce(
            self._presence.c.left_at, self._presence.c.confirmed_at
        )
        active = union_all(
            select(self._requests.c.requested_by.label("user_id")).where(
                self._requests.c.requested_at >= started_at,
                self._requests.c.requested_at < ended_at,
            ),
            select(self._listeners.c.user_id.label("user_id"))
            .join(
                self._playbacks,
                self._playbacks.c.id == self._listeners.c.playback_id,
            )
            .where(
                self._playbacks.c.started_at >= started_at,
                self._playbacks.c.started_at < ended_at,
            ),
            select(self._presence.c.user_id.label("user_id")).where(
                self._presence.c.joined_at < ended_at,
                known_end > started_at,
            ),
        ).subquery()
        value = await self._session.scalar(
            select(func.count(func.distinct(active.c.user_id)))
        )
        return int(value or 0)

    async def _request_totals(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None,
    ) -> RequestTotals:
        conditions = [
            self._requests.c.requested_at >= started_at,
            self._requests.c.requested_at < ended_at,
        ]
        if user_id is not None:
            conditions.append(self._requests.c.requested_by == user_id)
        row = (
            (
                await self._session.execute(
                    select(
                        func.count()
                        .filter(self._requests.c.origin == "manual")
                        .label("manual"),
                        func.count()
                        .filter(self._requests.c.origin == "radio")
                        .label("radio"),
                    ).where(and_(*conditions))
                )
            )
            .mappings()
            .one()
        )
        return RequestTotals(int(row["manual"] or 0), int(row["radio"] or 0))

    async def _playback_breakdown(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None,
    ) -> PlaybackBreakdown:
        relation: FromClause = self._playbacks.join(
            self._requests,
            self._requests.c.id == self._playbacks.c.request_id,
        )
        conditions = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        ]
        if user_id is not None:
            relation = relation.join(
                self._listeners,
                and_(
                    self._listeners.c.playback_id == self._playbacks.c.id,
                    self._listeners.c.user_id == user_id,
                ),
            )
        rows = (
            (
                await self._session.execute(
                    select(
                        self._requests.c.origin,
                        func.count(func.distinct(self._playbacks.c.id)).label(
                            "started"
                        ),
                        func.count(func.distinct(self._playbacks.c.id))
                        .filter(self._playbacks.c.end_reason == "completed")
                        .label("completed"),
                        func.count(func.distinct(self._playbacks.c.id))
                        .filter(self._playbacks.c.end_reason == "skipped")
                        .label("skipped"),
                        func.count(func.distinct(self._playbacks.c.id))
                        .filter(self._playbacks.c.end_reason == "stopped")
                        .label("stopped"),
                        func.count(func.distinct(self._playbacks.c.id))
                        .filter(self._playbacks.c.end_reason == "failed")
                        .label("failed"),
                    )
                    .select_from(relation)
                    .where(and_(*conditions))
                    .group_by(self._requests.c.origin)
                )
            )
            .mappings()
            .all()
        )
        by_origin = {
            row["origin"]: PlaybackOutcomes(
                started=int(row["started"]),
                completed=int(row["completed"]),
                skipped=int(row["skipped"]),
                stopped=int(row["stopped"]),
                failed=int(row["failed"]),
            )
            for row in rows
        }
        empty = PlaybackOutcomes(0, 0, 0, 0, 0)
        manual = by_origin.get("manual", empty)
        radio = by_origin.get("radio", empty)
        overall = PlaybackOutcomes(
            started=manual.started + radio.started,
            completed=manual.completed + radio.completed,
            skipped=manual.skipped + radio.skipped,
            stopped=manual.stopped + radio.stopped,
            failed=manual.failed + radio.failed,
        )
        return PlaybackBreakdown(overall, manual, radio)

    async def _listening_totals(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None,
    ) -> tuple[float, float, int, int]:
        relation, conditions, audio_seconds = self._listening_scope(
            started_at,
            ended_at,
            user_id,
        )
        row = (
            (
                await self._session.execute(
                    select(
                        func.coalesce(
                            func.sum(self._playbacks.c.group_audio_seconds), 0.0
                        ).label("playback_seconds"),
                        func.coalesce(func.sum(audio_seconds), 0.0).label(
                            "listening_seconds"
                        ),
                        func.count(func.distinct(self._requests.c.track_id)).label(
                            "unique_tracks"
                        ),
                    )
                    .select_from(relation)
                    .where(and_(*conditions))
                )
            )
            .mappings()
            .one()
        )
        artist_relation = relation.join(
            self._track_artists,
            self._track_artists.c.track_id == self._requests.c.track_id,
        )
        unique_artists = await self._session.scalar(
            select(func.count(func.distinct(self._track_artists.c.artist_id)))
            .select_from(artist_relation)
            .where(and_(*conditions))
        )
        return (
            float(row["playback_seconds"] or 0.0),
            float(row["listening_seconds"] or 0.0),
            int(row["unique_tracks"] or 0),
            int(unique_artists or 0),
        )

    async def _average_manual_wait(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None,
    ) -> float | None:
        conditions = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
            self._requests.c.origin == "manual",
        ]
        if user_id is not None:
            conditions.append(self._requests.c.requested_by == user_id)
        value = await self._session.scalar(
            select(
                func.avg(
                    func.extract(
                        "epoch",
                        self._playbacks.c.started_at - self._requests.c.requested_at,
                    )
                )
            )
            .select_from(
                self._playbacks.join(
                    self._requests,
                    self._requests.c.id == self._playbacks.c.request_id,
                )
            )
            .where(and_(*conditions))
        )
        return float(value) if value is not None else None

    def _artist_names(self) -> ScalarSelect[list[str] | None]:
        return (
            select(
                func.array_agg(
                    aggregate_order_by(
                        self._artists.c.name,
                        self._track_artists.c.position,
                    )
                )
            )
            .select_from(
                self._track_artists.join(
                    self._artists,
                    self._artists.c.id == self._track_artists.c.artist_id,
                )
            )
            .where(self._track_artists.c.track_id == self._tracks.c.id)
            .scalar_subquery()
        )

    @staticmethod
    def _listener_identity(row: RowMapping, prefix: str) -> ListenerIdentity:
        return ListenerIdentity(
            user_id=UserId(row[f"{prefix}_user_id"]),
            discord_id=row[f"{prefix}_discord_id"],
            display_name=row[f"{prefix}_display_name"],
            discord_username=row[f"{prefix}_discord_username"],
            discord_avatar_hash=row[f"{prefix}_discord_avatar_hash"],
        )

    def _listening_scope(
        self,
        started_at: datetime,
        ended_at: datetime,
        user_id: UserId | None,
    ) -> tuple[
        FromClause,
        list[ColumnElement[bool]],
        ColumnElement[float],
    ]:
        relation: FromClause = self._playbacks.join(
            self._requests,
            self._requests.c.id == self._playbacks.c.request_id,
        )
        listener_audio = self._listener_audio(user_id)
        relation = relation.outerjoin(
            listener_audio,
            listener_audio.c.playback_id == self._playbacks.c.id,
        )
        conditions: list[ColumnElement[bool]] = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        ]
        if user_id is not None:
            conditions.append(listener_audio.c.playback_id.is_not(None))
        return relation, conditions, func.coalesce(listener_audio.c.audio_seconds, 0.0)

    def _listener_audio(self, user_id: UserId | None) -> Subquery:
        statement = select(
            self._listeners.c.playback_id,
            func.sum(self._listeners.c.audio_seconds).label("audio_seconds"),
        )
        if user_id is not None:
            statement = statement.where(self._listeners.c.user_id == user_id)
        return statement.group_by(self._listeners.c.playback_id).subquery()


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as error:
        raise RuntimeError(f"Statistics table is not registered: {name}") from error
