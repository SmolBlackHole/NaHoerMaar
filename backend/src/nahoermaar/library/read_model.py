# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Queryable Library projections enriched from registered foreign tables."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from nahoermaar.catalog.domain import TrackId
from nahoermaar.database.schema import registered_table
from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.users.domain import UserId

from .domain import ReactionValue


@dataclass(frozen=True, slots=True)
class LibrarySnapshot:
    updated_at: datetime
    identifier: UUID


@dataclass(frozen=True, slots=True)
class ReactionSummary:
    track_id: TrackId
    reaction: ReactionValue | None
    likes: int
    dislikes: int


@dataclass(frozen=True, slots=True)
class LibraryTrack:
    track_id: TrackId
    title: str
    artist_names: tuple[str, ...]
    duration_seconds: float | None
    artwork_url: str | None
    album_title: str | None
    release_date: date | None
    isrc: str | None
    reaction: ReactionValue
    reacted_at: datetime
    likes: int
    dislikes: int


@dataclass(frozen=True, slots=True)
class LibraryTrackPage:
    entries: tuple[LibraryTrack, ...]
    page: int
    page_size: int
    total: int
    page_count: int
    snapshot: LibrarySnapshot | None


@dataclass(frozen=True, slots=True)
class ReactionParticipant:
    user_id: UserId
    display_name: str
    discord_id: str | None
    discord_avatar_hash: str | None
    reaction: ReactionValue
    reacted_at: datetime


@dataclass(frozen=True, slots=True)
class ReactionParticipantPage:
    entries: tuple[ReactionParticipant, ...]
    page: int
    page_size: int
    total: int
    page_count: int
    snapshot: LibrarySnapshot | None


class LibraryReadModel:
    """Read stable personal collections and aggregate reaction summaries."""

    __slots__ = (
        "_artists",
        "_discord",
        "_profiles",
        "_reactions",
        "_track_artists",
        "_tracks",
        "_units",
    )

    def __init__(self, units: UnitOfWorkFactory) -> None:
        self._units = units
        self._reactions = registered_table("track_reactions", consumer="Library")
        self._tracks = registered_table("tracks", consumer="Library")
        self._track_artists = registered_table("track_artists", consumer="Library")
        self._artists = registered_table("artists", consumer="Library")
        self._profiles = registered_table("user_profiles", consumer="Library")
        self._discord = registered_table("discord_identities", consumer="Library")

    async def summaries(
        self,
        user_id: UserId,
        track_ids: tuple[TrackId, ...],
    ) -> tuple[ReactionSummary, ...]:
        unique_ids = tuple(dict.fromkeys(track_ids))
        if not unique_ids:
            return ()
        async with self._units() as work:
            count_rows = (
                (
                    await work.session.execute(
                        select(
                            self._reactions.c.track_id,
                            func.count()
                            .filter(self._reactions.c.value == ReactionValue.LIKE.value)
                            .label("likes"),
                            func.count()
                            .filter(
                                self._reactions.c.value == ReactionValue.DISLIKE.value
                            )
                            .label("dislikes"),
                        )
                        .where(self._reactions.c.track_id.in_(unique_ids))
                        .group_by(self._reactions.c.track_id)
                    )
                )
                .mappings()
                .all()
            )
            reaction_rows = (
                (
                    await work.session.execute(
                        select(
                            self._reactions.c.track_id,
                            self._reactions.c.value,
                        ).where(
                            self._reactions.c.user_id == user_id,
                            self._reactions.c.track_id.in_(unique_ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
        counts = {
            TrackId(row["track_id"]): (int(row["likes"]), int(row["dislikes"]))
            for row in count_rows
        }
        reactions = {
            TrackId(row["track_id"]): ReactionValue(row["value"])
            for row in reaction_rows
        }
        return tuple(
            ReactionSummary(
                track_id,
                reactions.get(track_id),
                counts.get(track_id, (0, 0))[0],
                counts.get(track_id, (0, 0))[1],
            )
            for track_id in unique_ids
        )

    async def tracks(
        self,
        user_id: UserId,
        *,
        reaction: ReactionValue,
        page: int,
        page_size: int,
        query: str | None = None,
        snapshot: LibrarySnapshot | None = None,
    ) -> LibraryTrackPage:
        _validate_page(page, page_size)
        counts = self._counts()
        relation = self._reactions.join(
            self._tracks, self._tracks.c.id == self._reactions.c.track_id
        ).join(counts, counts.c.track_id == self._reactions.c.track_id)
        filters: list[Any] = [
            self._reactions.c.user_id == user_id,
            self._reactions.c.value == reaction.value,
        ]
        filters.extend(self._search_filters(query))
        async with self._units() as work:
            if snapshot is None:
                newest = (
                    await work.session.execute(
                        select(
                            self._reactions.c.updated_at,
                            self._reactions.c.track_id,
                        )
                        .select_from(relation)
                        .where(*filters)
                        .order_by(
                            self._reactions.c.updated_at.desc(),
                            self._reactions.c.track_id.desc(),
                        )
                        .limit(1)
                    )
                ).first()
                if newest is not None:
                    snapshot = LibrarySnapshot(newest.updated_at, newest.track_id)
            filters.extend(self._snapshot_filter(snapshot, self._reactions.c.track_id))
            total = int(
                await work.session.scalar(
                    select(func.count()).select_from(relation).where(*filters)
                )
                or 0
            )
            artist_names = self._artist_names()
            rows = (
                (
                    await work.session.execute(
                        select(
                            self._tracks.c.id.label("track_id"),
                            self._tracks.c.title,
                            self._tracks.c.duration_seconds,
                            self._tracks.c.artwork_url,
                            self._tracks.c.album_title,
                            self._tracks.c.release_date,
                            self._tracks.c.isrc,
                            self._reactions.c.value,
                            self._reactions.c.updated_at,
                            counts.c.likes,
                            counts.c.dislikes,
                            artist_names.label("artist_names"),
                        )
                        .select_from(relation)
                        .where(*filters)
                        .order_by(
                            self._reactions.c.updated_at.desc(),
                            self._reactions.c.track_id.desc(),
                        )
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .mappings()
                .all()
            )
        return LibraryTrackPage(
            tuple(self._track(row) for row in rows),
            page,
            page_size,
            total,
            (total + page_size - 1) // page_size,
            snapshot,
        )

    async def participants(
        self,
        track_id: TrackId,
        *,
        reaction: ReactionValue,
        page: int,
        page_size: int,
        snapshot: LibrarySnapshot | None = None,
    ) -> ReactionParticipantPage:
        _validate_page(page, page_size)
        relation = self._reactions.outerjoin(
            self._profiles, self._profiles.c.user_id == self._reactions.c.user_id
        ).outerjoin(self._discord, self._discord.c.user_id == self._reactions.c.user_id)
        filters: list[Any] = [
            self._reactions.c.track_id == track_id,
            self._reactions.c.value == reaction.value,
        ]
        async with self._units() as work:
            if snapshot is None:
                newest = (
                    await work.session.execute(
                        select(
                            self._reactions.c.updated_at,
                            self._reactions.c.user_id,
                        )
                        .select_from(relation)
                        .where(*filters)
                        .order_by(
                            self._reactions.c.updated_at.desc(),
                            self._reactions.c.user_id.desc(),
                        )
                        .limit(1)
                    )
                ).first()
                if newest is not None:
                    snapshot = LibrarySnapshot(newest.updated_at, newest.user_id)
            filters.extend(self._snapshot_filter(snapshot, self._reactions.c.user_id))
            total = int(
                await work.session.scalar(
                    select(func.count()).select_from(relation).where(*filters)
                )
                or 0
            )
            rows = (
                (
                    await work.session.execute(
                        select(
                            self._reactions.c.user_id,
                            self._reactions.c.value,
                            self._reactions.c.updated_at,
                            self._profiles.c.display_name,
                            self._discord.c.username,
                            self._discord.c.discord_id,
                            self._discord.c.avatar_hash,
                        )
                        .select_from(relation)
                        .where(*filters)
                        .order_by(
                            self._reactions.c.updated_at.desc(),
                            self._reactions.c.user_id.desc(),
                        )
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .mappings()
                .all()
            )
        return ReactionParticipantPage(
            tuple(self._participant(row) for row in rows),
            page,
            page_size,
            total,
            (total + page_size - 1) // page_size,
            snapshot,
        )

    def _counts(self) -> Any:
        return (
            select(
                self._reactions.c.track_id,
                func.count()
                .filter(self._reactions.c.value == ReactionValue.LIKE.value)
                .label("likes"),
                func.count()
                .filter(self._reactions.c.value == ReactionValue.DISLIKE.value)
                .label("dislikes"),
            )
            .group_by(self._reactions.c.track_id)
            .subquery()
        )

    def _artist_names(self) -> Any:
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

    def _search_filters(self, query: str | None) -> tuple[Any, ...]:
        normalized = query.strip() if query is not None else ""
        if not normalized:
            return ()
        pattern = f"%{normalized}%"
        matching_artist = (
            select(1)
            .select_from(
                self._track_artists.join(
                    self._artists,
                    self._artists.c.id == self._track_artists.c.artist_id,
                )
            )
            .where(
                self._track_artists.c.track_id == self._tracks.c.id,
                self._artists.c.name.ilike(pattern),
            )
            .exists()
        )
        return (or_(self._tracks.c.title.ilike(pattern), matching_artist),)

    def _snapshot_filter(
        self, snapshot: LibrarySnapshot | None, column: Any
    ) -> tuple[Any, ...]:
        if snapshot is None:
            return ()
        return (
            or_(
                self._reactions.c.updated_at < snapshot.updated_at,
                and_(
                    self._reactions.c.updated_at == snapshot.updated_at,
                    column <= snapshot.identifier,
                ),
            ),
        )

    @staticmethod
    def _track(row: Any) -> LibraryTrack:
        return LibraryTrack(
            TrackId(row["track_id"]),
            cast(str, row["title"]),
            tuple(cast(list[str] | None, row["artist_names"]) or ()),
            row["duration_seconds"],
            row["artwork_url"],
            row["album_title"],
            row["release_date"],
            row["isrc"],
            ReactionValue(row["value"]),
            row["updated_at"],
            int(row["likes"]),
            int(row["dislikes"]),
        )

    @staticmethod
    def _participant(row: Any) -> ReactionParticipant:
        user_id = UserId(row["user_id"])
        username = cast(str | None, row["username"])
        display_name = cast(str | None, row["display_name"])
        return ReactionParticipant(
            user_id,
            display_name or username or f"Listener {str(user_id)[:8]}",
            row["discord_id"],
            row["avatar_hash"],
            ReactionValue(row["value"]),
            row["updated_at"],
        )


def _validate_page(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError("Library page must be positive.")
    if not 1 <= page_size <= 100:
        raise ValueError("Library page size must be between 1 and 100.")
