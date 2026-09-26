# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Direct PostgreSQL projections over requests, plays and listener facts."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Date, Table, and_, cast as sql_cast, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import FromClause

from nahoermaar.database.schema import Base
from nahoermaar.users.domain import UserId


@dataclass(frozen=True, slots=True)
class Totals:
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


@dataclass(frozen=True, slots=True)
class DailyActivity:
    day: date
    plays: int
    listening_seconds: float


@dataclass(frozen=True, slots=True)
class RankedTrack:
    track_id: UUID
    title: str
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
    pixabot: str | None
    plays: int
    listening_seconds: float


class StatisticsRepository:
    """Execute read-only aggregate queries without hydrating domain aggregates."""

    __slots__ = (
        "_artists",
        "_discord",
        "_listeners",
        "_playbacks",
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
        ).subquery()
        value = await self._session.scalar(select(func.min(events.c.occurred_at)))
        return cast(datetime | None, value)

    async def totals(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None = None,
    ) -> Totals:
        request_conditions = [
            self._requests.c.requested_at >= started_at,
            self._requests.c.requested_at < ended_at,
        ]
        if user_id is not None:
            request_conditions.append(self._requests.c.requested_by == user_id)
        request_row = (
            (
                await self._session.execute(
                    select(
                        func.count().label("requests"),
                        func.count()
                        .filter(self._requests.c.origin == "manual")
                        .label("manual_requests"),
                        func.count()
                        .filter(self._requests.c.origin == "radio")
                        .label("radio_requests"),
                    ).where(and_(*request_conditions))
                )
            )
            .mappings()
            .one()
        )

        relation = self._playbacks.join(
            self._requests,
            self._requests.c.id == self._playbacks.c.request_id,
        )
        play_conditions = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        ]
        audio_seconds = self._playbacks.c.group_audio_seconds
        if user_id is not None:
            relation = relation.join(
                self._listeners,
                self._listeners.c.playback_id == self._playbacks.c.id,
            )
            play_conditions.append(self._listeners.c.user_id == user_id)
            audio_seconds = self._listeners.c.audio_seconds

        play_row = (
            (
                await self._session.execute(
                    select(
                        func.count(func.distinct(self._playbacks.c.id)).label("plays"),
                        func.count()
                        .filter(self._playbacks.c.end_reason == "completed")
                        .label("completed"),
                        func.count()
                        .filter(self._playbacks.c.end_reason == "skipped")
                        .label("skipped"),
                        func.count()
                        .filter(self._playbacks.c.end_reason == "stopped")
                        .label("stopped"),
                        func.count()
                        .filter(self._playbacks.c.end_reason == "failed")
                        .label("failed"),
                        func.coalesce(func.sum(audio_seconds), 0.0).label(
                            "listening_seconds"
                        ),
                        func.count(func.distinct(self._requests.c.track_id)).label(
                            "unique_tracks"
                        ),
                    )
                    .select_from(relation)
                    .where(and_(*play_conditions))
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
            .where(and_(*play_conditions))
        )

        wait_conditions = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        ]
        if user_id is not None:
            wait_conditions.append(self._requests.c.requested_by == user_id)
        average_wait = await self._session.scalar(
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
            .where(and_(*wait_conditions))
        )

        return Totals(
            requests=int(request_row["requests"] or 0),
            manual_requests=int(request_row["manual_requests"] or 0),
            radio_requests=int(request_row["radio_requests"] or 0),
            plays=int(play_row["plays"] or 0),
            completed=int(play_row["completed"] or 0),
            skipped=int(play_row["skipped"] or 0),
            stopped=int(play_row["stopped"] or 0),
            failed=int(play_row["failed"] or 0),
            listening_seconds=float(play_row["listening_seconds"] or 0.0),
            unique_tracks=int(play_row["unique_tracks"] or 0),
            unique_artists=int(unique_artists or 0),
            average_wait_seconds=(
                float(average_wait) if average_wait is not None else None
            ),
        )

    async def daily_activity(
        self,
        started_at: datetime,
        ended_at: datetime,
        timezone: str,
        *,
        user_id: UserId | None = None,
    ) -> tuple[DailyActivity, ...]:
        relation: FromClause = self._playbacks
        conditions = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        ]
        audio_seconds = self._playbacks.c.group_audio_seconds
        if user_id is not None:
            relation = relation.join(
                self._listeners,
                self._listeners.c.playback_id == self._playbacks.c.id,
            )
            conditions.append(self._listeners.c.user_id == user_id)
            audio_seconds = self._listeners.c.audio_seconds
        local_day = sql_cast(
            func.timezone(timezone, self._playbacks.c.started_at),
            Date,
        ).label("day")
        plays = func.count(func.distinct(self._playbacks.c.id)).label("plays")
        listening = func.coalesce(func.sum(audio_seconds), 0.0).label(
            "listening_seconds"
        )
        rows = (
            (
                await self._session.execute(
                    select(local_day, plays, listening)
                    .select_from(relation)
                    .where(and_(*conditions))
                    .group_by(local_day)
                    .order_by(local_day)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            DailyActivity(
                day=row["day"],
                plays=int(row["plays"]),
                listening_seconds=float(row["listening_seconds"]),
            )
            for row in rows
        )

    async def top_tracks(
        self,
        started_at: datetime,
        ended_at: datetime,
        *,
        user_id: UserId | None = None,
        limit: int = 5,
    ) -> tuple[RankedTrack, ...]:
        relation = self._playbacks.join(
            self._requests,
            self._requests.c.id == self._playbacks.c.request_id,
        ).join(self._tracks, self._tracks.c.id == self._requests.c.track_id)
        conditions = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        ]
        audio_seconds = self._playbacks.c.group_audio_seconds
        if user_id is not None:
            relation = relation.join(
                self._listeners,
                self._listeners.c.playback_id == self._playbacks.c.id,
            )
            conditions.append(self._listeners.c.user_id == user_id)
            audio_seconds = self._listeners.c.audio_seconds
        plays = func.count(func.distinct(self._playbacks.c.id)).label("plays")
        listening = func.coalesce(func.sum(audio_seconds), 0.0).label(
            "listening_seconds"
        )
        rows = (
            (
                await self._session.execute(
                    select(
                        self._tracks.c.id,
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
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
        relation = (
            self._playbacks.join(
                self._requests,
                self._requests.c.id == self._playbacks.c.request_id,
            )
            .join(
                self._track_artists,
                self._track_artists.c.track_id == self._requests.c.track_id,
            )
            .join(
                self._artists,
                self._artists.c.id == self._track_artists.c.artist_id,
            )
        )
        conditions = [
            self._playbacks.c.started_at >= started_at,
            self._playbacks.c.started_at < ended_at,
        ]
        audio_seconds = self._playbacks.c.group_audio_seconds
        if user_id is not None:
            relation = relation.join(
                self._listeners,
                self._listeners.c.playback_id == self._playbacks.c.id,
            )
            conditions.append(self._listeners.c.user_id == user_id)
            audio_seconds = self._listeners.c.audio_seconds
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
        limit: int = 5,
    ) -> tuple[RankedListener, ...]:
        relation = (
            self._listeners.join(
                self._playbacks,
                self._playbacks.c.id == self._listeners.c.playback_id,
            )
            .join(self._users, self._users.c.id == self._listeners.c.user_id)
            .join(
                self._profiles,
                self._profiles.c.user_id == self._listeners.c.user_id,
            )
            .join(
                self._discord,
                self._discord.c.user_id == self._listeners.c.user_id,
            )
        )
        plays = func.count(func.distinct(self._playbacks.c.id)).label("plays")
        listening = func.sum(self._listeners.c.audio_seconds).label("listening_seconds")
        rows = (
            (
                await self._session.execute(
                    select(
                        self._users.c.id,
                        self._discord.c.discord_id,
                        self._profiles.c.display_name,
                        self._profiles.c.pixabot,
                        self._discord.c.username,
                        plays,
                        listening,
                    )
                    .select_from(relation)
                    .where(
                        self._playbacks.c.started_at >= started_at,
                        self._playbacks.c.started_at < ended_at,
                        self._users.c.role.is_not(None),
                    )
                    .group_by(
                        self._users.c.id,
                        self._discord.c.discord_id,
                        self._profiles.c.display_name,
                        self._profiles.c.pixabot,
                        self._discord.c.username,
                    )
                    .order_by(listening.desc(), plays.desc(), self._users.c.id)
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
                pixabot=row["pixabot"],
                plays=int(row["plays"]),
                listening_seconds=float(row["listening_seconds"]),
            )
            for row in rows
        )


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as error:
        raise RuntimeError(f"Statistics table is not registered: {name}") from error
