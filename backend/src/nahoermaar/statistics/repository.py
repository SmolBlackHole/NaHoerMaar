# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Direct PostgreSQL projections over requests, plays and listener facts."""

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import Date, Table, and_, cast as sql_cast, func, or_, select, union_all
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, FromClause
from sqlalchemy.sql.selectable import Subquery

from nahoermaar.database.schema import Base
from nahoermaar.users.domain import UserId

from .models import (
    ActivityBucket,
    ActivityGranularity,
    PlaybackBreakdown,
    PlaybackOutcomes,
    RankedArtist,
    RankedListener,
    RankedTrack,
    RequestTotals,
    StatisticsTotals,
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
        listening_seconds, unique_tracks, unique_artists = await self._listening_totals(
            started_at,
            ended_at,
            user_id=user_id,
        )
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
        rows = (
            (
                await self._session.execute(
                    select(bucket, plays, listening)
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
                    .order_by(plays.desc(), listening.desc(), self._tracks.c.id)
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
                    .order_by(plays.desc(), listening.desc(), self._artists.c.id)
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

    async def top_listeners(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        limit: int = 8,
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
        listening = (
            select(
                self._listeners.c.user_id,
                func.count(func.distinct(self._listeners.c.playback_id)).label("plays"),
                func.sum(self._listeners.c.audio_seconds).label("listening_seconds"),
            )
            .join(
                self._playbacks,
                self._playbacks.c.id == self._listeners.c.playback_id,
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
            .outerjoin(listening, listening.c.user_id == self._users.c.id)
            .outerjoin(presence, presence.c.user_id == self._users.c.id)
        )
        request_count = func.coalesce(manual_requests.c.manual_requests, 0)
        play_count = func.coalesce(listening.c.plays, 0)
        heard = func.coalesce(listening.c.listening_seconds, 0.0)
        present = func.coalesce(presence.c.presence_seconds, 0.0)
        rows = (
            (
                await self._session.execute(
                    select(
                        self._users.c.id,
                        self._discord.c.discord_id,
                        self._profiles.c.display_name,
                        self._discord.c.username,
                        self._discord.c.avatar_hash,
                        request_count.label("manual_requests"),
                        play_count.label("plays"),
                        present.label("presence_seconds"),
                        heard.label("listening_seconds"),
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
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            RankedListener(
                user_id=UserId(row["id"]),
                discord_id=row["discord_id"],
                display_name=row["display_name"],
                discord_username=row["username"],
                discord_avatar_hash=row["avatar_hash"],
                manual_requests=int(row["manual_requests"]),
                plays=int(row["plays"]),
                presence_seconds=float(row["presence_seconds"]),
                listening_seconds=float(row["listening_seconds"]),
            )
            for row in rows
        )

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
    ) -> tuple[float, int, int]:
        relation, conditions, audio_seconds = self._listening_scope(
            started_at,
            ended_at,
            user_id,
        )
        row = (
            (
                await self._session.execute(
                    select(
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
