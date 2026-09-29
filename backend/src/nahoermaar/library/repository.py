# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Transactional persistence owned by the personal Library."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.database.schema import Base, enum_values
from nahoermaar.users.domain import UserId

from .domain import (
    MAX_PLAYLIST_ENTRIES,
    LibraryError,
    LibraryErrorCode,
    Playlist,
    PlaylistEntryId,
    PlaylistId,
    PlaylistTrackSelection,
    ReactionValue,
    TrackReaction,
)


_REACTION = SqlEnum(
    ReactionValue,
    name="track_reaction_value",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=enum_values,
)


class _TrackReactionRow(Base):
    __tablename__ = "track_reactions"
    __table_args__ = (
        CheckConstraint("updated_at >= created_at", name="update_not_before_creation"),
        Index(
            "ix_track_reactions_user_value_updated_track",
            "user_id",
            "value",
            "updated_at",
            "track_id",
        ),
        Index("ix_track_reactions_track_value", "track_id", "value"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), primary_key=True
    )
    value: Mapped[ReactionValue] = mapped_column(_REACTION)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _PlaylistRow(Base):
    __tablename__ = "playlists"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100 AND name = btrim(name)",
            name="name_valid",
        ),
        CheckConstraint("updated_at >= created_at", name="update_not_before_creation"),
        Index("ix_playlists_owner_updated_id", "owner_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _PlaylistEntryRow(Base):
    __tablename__ = "playlist_entries"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_nonnegative"),
        UniqueConstraint(
            "playlist_id",
            "position",
            name="uq_playlist_entries_playlist_position",
        ),
        Index("ix_playlist_entries_track_id", "track_id"),
        Index("ix_playlist_entries_preferred_source_id", "preferred_source_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    playlist_id: Mapped[UUID] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False
    )
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    preferred_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("track_sources.id", ondelete="RESTRICT"), nullable=True
    )
    added_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrackReactionRepository:
    """Write reactions inside an existing transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId, track_id: TrackId) -> TrackReaction | None:
        row = await self._session.scalar(
            select(_TrackReactionRow).where(
                _TrackReactionRow.user_id == user_id,
                _TrackReactionRow.track_id == track_id,
            )
        )
        return _reaction(row) if row is not None else None

    async def set(
        self,
        user_id: UserId,
        track_id: TrackId,
        value: ReactionValue,
        now: datetime,
    ) -> TrackReaction:
        row = await self._session.scalar(
            select(_TrackReactionRow)
            .where(
                _TrackReactionRow.user_id == user_id,
                _TrackReactionRow.track_id == track_id,
            )
            .with_for_update()
        )
        if row is None:
            row = _TrackReactionRow(
                user_id=user_id,
                track_id=track_id,
                value=value,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
            await self._session.flush()
        elif row.value is not value:
            row.value = value
            row.updated_at = now
            await self._session.flush()
        return _reaction(row)

    async def remove(self, user_id: UserId, track_id: TrackId) -> bool:
        row = await self._session.scalar(
            select(_TrackReactionRow)
            .where(
                _TrackReactionRow.user_id == user_id,
                _TrackReactionRow.track_id == track_id,
            )
            .with_for_update()
        )
        if row is None:
            return False
        await self._session.delete(row)
        return True


class PlaylistRepository:
    """Write personal playlists inside an existing transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        owner_id: UserId,
        name: str,
        now: datetime,
    ) -> Playlist:
        row = _PlaylistRow(
            id=uuid4(),
            owner_id=owner_id,
            name=name,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return _playlist(row)

    async def get(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
    ) -> Playlist:
        return _playlist(await self._owned(owner_id, playlist_id))

    async def rename(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        name: str,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row = await self._owned(
            owner_id,
            playlist_id,
            expected_revision=expected_revision,
            for_update=True,
        )
        if row.name != name:
            row.name = name
            self._touch(row, now)
            await self._session.flush()
        return _playlist(row)

    async def delete(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        expected_revision: int,
    ) -> Playlist:
        row = await self._owned(
            owner_id,
            playlist_id,
            expected_revision=expected_revision,
            for_update=True,
        )
        deleted = _playlist(row)
        await self._session.delete(row)
        await self._session.flush()
        return deleted

    async def duplicate(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        name: str,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        await self._owned(
            owner_id,
            playlist_id,
            expected_revision=expected_revision,
            for_update=True,
        )
        source_entries = await self._entry_rows(playlist_id, for_update=True)
        duplicate = await self.create(owner_id, name, now)
        self._session.add_all(
            [
                _PlaylistEntryRow(
                    id=uuid4(),
                    playlist_id=duplicate.id,
                    track_id=entry.track_id,
                    preferred_source_id=entry.preferred_source_id,
                    added_by=entry.added_by,
                    position=entry.position,
                    created_at=now,
                )
                for entry in source_entries
            ]
        )
        await self._session.flush()
        return duplicate

    async def add(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        selections: tuple[PlaylistTrackSelection, ...],
        added_by: UserId,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row = await self._owned(
            owner_id,
            playlist_id,
            expected_revision=expected_revision,
            for_update=True,
        )
        entries = await self._entry_rows(playlist_id, for_update=True)
        if len(entries) + len(selections) > MAX_PLAYLIST_ENTRIES:
            raise LibraryError(LibraryErrorCode.PLAYLIST_CAPACITY_EXCEEDED, 409)
        self._session.add_all(
            [
                _PlaylistEntryRow(
                    id=uuid4(),
                    playlist_id=playlist_id,
                    track_id=selection.track_id,
                    preferred_source_id=selection.preferred_source_id,
                    added_by=added_by,
                    position=len(entries) + index,
                    created_at=now,
                )
                for index, selection in enumerate(selections)
            ]
        )
        if selections:
            self._touch(row, now)
        await self._session.flush()
        return _playlist(row)

    async def remove(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        entry_id: PlaylistEntryId,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row = await self._owned(
            owner_id,
            playlist_id,
            expected_revision=expected_revision,
            for_update=True,
        )
        entries = list(await self._entry_rows(playlist_id, for_update=True))
        target = next((entry for entry in entries if entry.id == entry_id), None)
        if target is None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_ENTRY_NOT_FOUND, 404)
        entries.remove(target)
        await self._session.delete(target)
        await self._session.flush()
        await self._reposition(entries)
        self._touch(row, now)
        await self._session.flush()
        return _playlist(row)

    async def reorder(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        entry_ids: tuple[PlaylistEntryId, ...],
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row = await self._owned(
            owner_id,
            playlist_id,
            expected_revision=expected_revision,
            for_update=True,
        )
        entries = await self._entry_rows(playlist_id, for_update=True)
        by_id = {PlaylistEntryId(entry.id): entry for entry in entries}
        if len(entry_ids) != len(entries) or set(entry_ids) != set(by_id):
            raise LibraryError(LibraryErrorCode.PLAYLIST_ORDER_INVALID, 422)
        ordered = [by_id[entry_id] for entry_id in entry_ids]
        if tuple(entry.id for entry in ordered) != tuple(entry.id for entry in entries):
            await self._reposition(ordered)
            self._touch(row, now)
            await self._session.flush()
        return _playlist(row)

    async def selections(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        *,
        expected_revision: int | None = None,
    ) -> tuple[PlaylistTrackSelection, ...]:
        await self._owned(
            owner_id,
            playlist_id,
            expected_revision=expected_revision,
        )
        return tuple(
            PlaylistTrackSelection(
                TrackId(entry.track_id),
                TrackSourceId(entry.preferred_source_id)
                if entry.preferred_source_id is not None
                else None,
            )
            for entry in await self._entry_rows(playlist_id)
        )

    async def _owned(
        self,
        owner_id: UserId,
        playlist_id: PlaylistId,
        *,
        expected_revision: int | None = None,
        for_update: bool = False,
    ) -> _PlaylistRow:
        statement = select(_PlaylistRow).where(
            _PlaylistRow.id == playlist_id,
            _PlaylistRow.owner_id == owner_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        if row is None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_NOT_FOUND, 404)
        if expected_revision is not None and row.revision != expected_revision:
            raise LibraryError(LibraryErrorCode.PLAYLIST_REVISION_CONFLICT, 409)
        return row

    async def _entry_rows(
        self,
        playlist_id: PlaylistId,
        *,
        for_update: bool = False,
    ) -> tuple[_PlaylistEntryRow, ...]:
        statement = (
            select(_PlaylistEntryRow)
            .where(_PlaylistEntryRow.playlist_id == playlist_id)
            .order_by(_PlaylistEntryRow.position)
        )
        if for_update:
            statement = statement.with_for_update()
        return tuple((await self._session.scalars(statement)).all())

    async def _reposition(self, entries: list[_PlaylistEntryRow]) -> None:
        if not entries:
            return
        offset = len(entries) + 1
        for entry in entries:
            entry.position += offset
        await self._session.flush()
        for position, entry in enumerate(entries):
            entry.position = position
        await self._session.flush()

    @staticmethod
    def _touch(row: _PlaylistRow, now: datetime) -> None:
        row.revision += 1
        row.updated_at = now


def _reaction(row: _TrackReactionRow) -> TrackReaction:
    return TrackReaction(
        UserId(row.user_id),
        TrackId(row.track_id),
        row.value,
        row.created_at,
        row.updated_at,
    )


def _playlist(row: _PlaylistRow) -> Playlist:
    return Playlist(
        PlaylistId(row.id),
        UserId(row.owner_id),
        row.name,
        row.revision,
        row.created_at,
        row.updated_at,
    )
