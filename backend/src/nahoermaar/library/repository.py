# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Transactional persistence owned by the personal Library."""

from collections import defaultdict, deque
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.database.schema import Base, enum_values
from nahoermaar.users.domain import UserId

from .domain import (
    MAX_PLAYLIST_ENTRIES,
    PLAYLIST_ENTRY_UNDO_LIFETIME,
    LibraryError,
    LibraryErrorCode,
    Playlist,
    PlaylistAccess,
    PlaylistEntry,
    PlaylistEntryId,
    PlaylistEntryUndo,
    PlaylistEntryUndoId,
    PlaylistId,
    PlaylistSource,
    PlaylistSyncChanges,
    PlaylistTrackSelection,
    PlaylistVisibility,
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

_VISIBILITY = SqlEnum(
    PlaylistVisibility,
    name="playlist_visibility",
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
        CheckConstraint("owner_position >= 0", name="owner_position_nonnegative"),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100 AND name = btrim(name)",
            name="name_valid",
        ),
        CheckConstraint("updated_at >= created_at", name="update_not_before_creation"),
        CheckConstraint(
            "((source_provider_key IS NULL AND source_external_id IS NULL AND "
            "source_url IS NULL) OR (source_provider_key IS NOT NULL AND "
            "source_external_id IS NOT NULL AND source_url IS NOT NULL))",
            name="source_identity_complete",
        ),
        CheckConstraint(
            "source_unavailable_entry_count >= 0",
            name="source_unavailable_nonnegative",
        ),
        CheckConstraint(
            "((source_provider_key IS NULL AND source_last_attempt_at IS NULL AND "
            "source_last_successful_sync_at IS NULL AND source_last_error_code IS NULL "
            "AND source_unavailable_entry_count = 0 AND source_truncated = false) OR "
            "(source_provider_key IS NOT NULL AND source_last_attempt_at IS NOT NULL "
            "AND source_last_successful_sync_at IS NOT NULL))",
            name="source_state_complete",
        ),
        CheckConstraint(
            "source_last_successful_sync_at IS NULL OR source_last_attempt_at IS NULL "
            "OR source_last_successful_sync_at <= source_last_attempt_at",
            name="source_success_not_after_attempt",
        ),
        UniqueConstraint(
            "owner_id",
            "source_provider_key",
            "source_external_id",
            name="uq_playlists_owner_source",
        ),
        UniqueConstraint(
            "owner_id",
            "owner_position",
            name="uq_playlists_owner_position",
        ),
        Index("ix_playlists_owner_updated_id", "owner_id", "updated_at", "id"),
        Index("ix_playlists_visibility_updated_id", "visibility", "updated_at", "id"),
        Index(
            "ix_playlists_source_due",
            "source_provider_key",
            "source_last_successful_sync_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    owner_position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    visibility: Mapped[PlaylistVisibility] = mapped_column(_VISIBILITY)
    source_provider_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_last_successful_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_last_error_code: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    source_unavailable_entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
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


class _PlaylistCollaboratorRow(Base):
    __tablename__ = "playlist_collaborators"
    __table_args__ = (
        Index("ix_playlist_collaborators_user_playlist", "user_id", "playlist_id"),
    )

    playlist_id: Mapped[UUID] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _PlaylistEntryUndoRow(Base):
    __tablename__ = "playlist_entry_undos"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("expires_at > removed_at", name="positive_lifetime"),
        Index("ix_playlist_entry_undos_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    playlist_id: Mapped[UUID] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[UUID] = mapped_column(nullable=False)
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
    entry_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    """Write playlists through transactional capability checks."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        owner_id: UserId,
        name: str,
        now: datetime,
    ) -> Playlist:
        owner_position = await self._next_owner_position(owner_id)
        row = _PlaylistRow(
            id=uuid4(),
            owner_id=owner_id,
            owner_position=owner_position,
            name=name,
            visibility=PlaylistVisibility.PRIVATE,
            source_provider_key=None,
            source_external_id=None,
            source_url=None,
            source_last_attempt_at=None,
            source_last_successful_sync_at=None,
            source_last_error_code=None,
            source_unavailable_entry_count=0,
            source_truncated=False,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return _playlist(row)

    async def import_linked(
        self,
        owner_id: UserId,
        name: str,
        *,
        provider_key: str,
        external_id: str,
        canonical_url: str,
        selections: tuple[PlaylistTrackSelection, ...],
        unavailable_entry_count: int,
        truncated: bool,
        now: datetime,
    ) -> tuple[Playlist, bool]:
        existing = await self._session.scalar(
            select(_PlaylistRow)
            .where(
                _PlaylistRow.owner_id == owner_id,
                _PlaylistRow.source_provider_key == provider_key,
                _PlaylistRow.source_external_id == external_id,
            )
            .with_for_update()
        )
        if existing is not None:
            return _playlist(existing), False
        if len(selections) > MAX_PLAYLIST_ENTRIES:
            raise LibraryError(LibraryErrorCode.PLAYLIST_CAPACITY_EXCEEDED, 409)
        owner_position = await self._next_owner_position(owner_id)
        row = _PlaylistRow(
            id=uuid4(),
            owner_id=owner_id,
            owner_position=owner_position,
            name=name,
            visibility=PlaylistVisibility.PRIVATE,
            source_provider_key=provider_key,
            source_external_id=external_id,
            source_url=canonical_url,
            source_last_attempt_at=now,
            source_last_successful_sync_at=now,
            source_last_error_code=None,
            source_unavailable_entry_count=unavailable_entry_count,
            source_truncated=truncated,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add_all(
            [
                _PlaylistEntryRow(
                    id=uuid4(),
                    playlist_id=row.id,
                    track_id=selection.track_id,
                    preferred_source_id=selection.preferred_source_id,
                    added_by=owner_id,
                    position=position,
                    created_at=now,
                )
                for position, selection in enumerate(selections)
            ]
        )
        await self._session.flush()
        return _playlist(row), True

    async def linked_sync_candidates(
        self,
        *,
        due_before: datetime,
        limit: int,
    ) -> tuple[Playlist, ...]:
        rows = await self._session.scalars(
            select(_PlaylistRow)
            .where(
                _PlaylistRow.source_provider_key.is_not(None),
                _PlaylistRow.source_last_attempt_at <= due_before,
            )
            .order_by(
                _PlaylistRow.source_last_successful_sync_at,
                _PlaylistRow.id,
            )
            .limit(limit)
        )
        return tuple(_playlist(row) for row in rows)

    async def synchronize_linked(
        self,
        playlist_id: PlaylistId,
        *,
        provider_key: str,
        external_id: str,
        canonical_url: str,
        selections: tuple[PlaylistTrackSelection, ...],
        unavailable_entry_count: int,
        truncated: bool,
        now: datetime,
    ) -> PlaylistSyncChanges | None:
        if len(selections) > MAX_PLAYLIST_ENTRIES:
            raise LibraryError(LibraryErrorCode.PLAYLIST_CAPACITY_EXCEEDED, 409)
        row = await self._session.scalar(
            select(_PlaylistRow).where(_PlaylistRow.id == playlist_id).with_for_update()
        )
        if (
            row is None
            or row.source_provider_key != provider_key
            or row.source_external_id != external_id
        ):
            return None

        existing = list(await self._entry_rows(playlist_id, for_update=True))
        original_positions = {entry.id: entry.position for entry in existing}
        by_source: dict[tuple[bool, UUID], deque[_PlaylistEntryRow]] = defaultdict(
            deque
        )
        for entry in existing:
            by_source[_entry_match_key(entry)].append(entry)

        offset = max(len(existing), len(selections)) + 1
        for entry in existing:
            entry.position += offset
        await self._session.flush()

        added = 0
        moved = 0
        unchanged = 0
        retained: set[UUID] = set()
        for position, selection in enumerate(selections):
            matches = by_source[_selection_match_key(selection)]
            if matches:
                entry = matches.popleft()
                retained.add(entry.id)
                if original_positions[entry.id] == position:
                    unchanged += 1
                else:
                    moved += 1
                entry.track_id = selection.track_id
                entry.preferred_source_id = selection.preferred_source_id
                entry.position = position
                continue
            self._session.add(
                _PlaylistEntryRow(
                    id=uuid4(),
                    playlist_id=row.id,
                    track_id=selection.track_id,
                    preferred_source_id=selection.preferred_source_id,
                    added_by=row.owner_id,
                    position=position,
                    created_at=now,
                )
            )
            added += 1

        removed = len(existing) - len(retained)
        for entry in existing:
            if entry.id not in retained:
                await self._session.delete(entry)

        changes = PlaylistSyncChanges(added, removed, moved, unchanged)
        row.source_url = canonical_url
        row.source_last_attempt_at = now
        row.source_last_successful_sync_at = now
        row.source_last_error_code = None
        row.source_unavailable_entry_count = unavailable_entry_count
        row.source_truncated = truncated
        if changes.content_changed:
            self._touch(row, now)
        await self._session.flush()
        return changes

    async def mark_linked_sync_failed(
        self,
        playlist_id: PlaylistId,
        *,
        provider_key: str,
        external_id: str,
        error_code: str,
        now: datetime,
    ) -> bool:
        row = await self._session.scalar(
            select(_PlaylistRow).where(_PlaylistRow.id == playlist_id).with_for_update()
        )
        if (
            row is None
            or row.source_provider_key != provider_key
            or row.source_external_id != external_id
        ):
            return False
        row.source_last_attempt_at = now
        row.source_last_error_code = error_code[:200]
        await self._session.flush()
        return True

    async def get(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset(PlaylistAccess),
        )
        return _playlist(row)

    async def update(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        name: str | None,
        visibility: PlaylistVisibility | None,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER}),
            expected_revision=expected_revision,
            for_update=True,
        )
        changed = False
        if name is not None and row.name != name:
            row.name = name
            changed = True
        if visibility is not None and row.visibility is not visibility:
            row.visibility = visibility
            changed = True
        if changed:
            self._touch(row, now)
            await self._session.flush()
        return _playlist(row)

    async def delete(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        expected_revision: int,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER}),
            expected_revision=expected_revision,
            for_update=True,
        )
        owned = list(
            await self._owned_playlist_rows(UserId(row.owner_id), for_update=True)
        )
        deleted = _playlist(row)
        await self._session.delete(row)
        await self._session.flush()
        owned.remove(row)
        await self._reposition_playlists(owned)
        return deleted

    async def move_playlist(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        position: int,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER}),
            for_update=True,
        )
        owned = list(await self._owned_playlist_rows(actor_id, for_update=True))
        if position < 0 or position >= len(owned):
            raise LibraryError(LibraryErrorCode.PLAYLIST_ORDER_INVALID, 422)
        original = tuple(item.id for item in owned)
        owned.remove(row)
        owned.insert(position, row)
        if tuple(item.id for item in owned) != original:
            await self._reposition_playlists(owned)
        return _playlist(row)

    async def duplicate(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        name: str,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER}),
            expected_revision=expected_revision,
            for_update=True,
        )
        source_entries = await self._entry_rows(playlist_id, for_update=True)
        duplicate = await self.create(actor_id, name, now)
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
        actor_id: UserId,
        playlist_id: PlaylistId,
        selections: tuple[PlaylistTrackSelection, ...],
        added_by: UserId,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER, PlaylistAccess.EDITOR}),
            expected_revision=expected_revision,
            for_update=True,
        )
        self._require_internal(row)
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
        actor_id: UserId,
        playlist_id: PlaylistId,
        entry_id: PlaylistEntryId,
        expected_revision: int,
        now: datetime,
    ) -> tuple[Playlist, PlaylistEntryUndo]:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER, PlaylistAccess.EDITOR}),
            expected_revision=expected_revision,
            for_update=True,
        )
        self._require_internal(row)
        entries = list(await self._entry_rows(playlist_id, for_update=True))
        target = next((entry for entry in entries if entry.id == entry_id), None)
        if target is None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_ENTRY_NOT_FOUND, 404)
        undo_row = _PlaylistEntryUndoRow(
            id=uuid4(),
            actor_id=actor_id,
            playlist_id=playlist_id,
            entry_id=target.id,
            track_id=target.track_id,
            preferred_source_id=target.preferred_source_id,
            added_by=target.added_by,
            position=target.position,
            entry_created_at=target.created_at,
            removed_at=now,
            expires_at=now + PLAYLIST_ENTRY_UNDO_LIFETIME,
        )
        self._session.add(undo_row)
        entries.remove(target)
        await self._session.delete(target)
        await self._session.flush()
        await self._reposition(entries)
        self._touch(row, now)
        await self._session.flush()
        return _playlist(row), _playlist_entry_undo(undo_row)

    async def restore(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        undo_id: PlaylistEntryUndoId,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER, PlaylistAccess.EDITOR}),
            expected_revision=expected_revision,
            for_update=True,
        )
        self._require_internal(row)
        undo_statement = (
            select(_PlaylistEntryUndoRow)
            .where(
                _PlaylistEntryUndoRow.id == undo_id,
                _PlaylistEntryUndoRow.actor_id == actor_id,
                _PlaylistEntryUndoRow.playlist_id == playlist_id,
            )
            .with_for_update()
        )
        undo = await self._session.scalar(undo_statement)
        if undo is None or undo.expires_at <= now:
            raise LibraryError(LibraryErrorCode.PLAYLIST_UNDO_UNAVAILABLE, 409)
        entries = list(await self._entry_rows(playlist_id, for_update=True))
        if undo.position > len(entries) or any(
            entry.id == undo.entry_id for entry in entries
        ):
            raise LibraryError(LibraryErrorCode.PLAYLIST_UNDO_UNAVAILABLE, 409)
        restored = _PlaylistEntryRow(
            id=undo.entry_id,
            playlist_id=playlist_id,
            track_id=undo.track_id,
            preferred_source_id=undo.preferred_source_id,
            added_by=undo.added_by,
            position=len(entries) + 1,
            created_at=undo.entry_created_at,
        )
        self._session.add(restored)
        await self._session.flush()
        entries.insert(undo.position, restored)
        await self._reposition(entries)
        await self._session.delete(undo)
        self._touch(row, now)
        await self._session.flush()
        return _playlist(row)

    async def prune_entry_undos(self, now: datetime, *, limit: int) -> int:
        identifiers = (
            select(_PlaylistEntryUndoRow.id)
            .where(_PlaylistEntryUndoRow.expires_at <= now)
            .order_by(_PlaylistEntryUndoRow.expires_at, _PlaylistEntryUndoRow.id)
            .limit(limit)
        )
        expired = tuple(await self._session.scalars(identifiers))
        if expired:
            await self._session.execute(
                delete(_PlaylistEntryUndoRow).where(
                    _PlaylistEntryUndoRow.id.in_(expired)
                )
            )
        return len(expired)

    async def move(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        entry_id: PlaylistEntryId,
        position: int,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER, PlaylistAccess.EDITOR}),
            expected_revision=expected_revision,
            for_update=True,
        )
        self._require_internal(row)
        entries = list(await self._entry_rows(playlist_id, for_update=True))
        target = next((entry for entry in entries if entry.id == entry_id), None)
        if target is None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_ENTRY_NOT_FOUND, 404)
        if position < 0 or position >= len(entries):
            raise LibraryError(LibraryErrorCode.PLAYLIST_ORDER_INVALID, 422)
        original = tuple(entry.id for entry in entries)
        entries.remove(target)
        entries.insert(position, target)
        if tuple(entry.id for entry in entries) != original:
            await self._reposition(entries)
            self._touch(row, now)
            await self._session.flush()
        return _playlist(row)

    async def detach(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER}),
            expected_revision=expected_revision,
            for_update=True,
        )
        if row.source_provider_key is None:
            return _playlist(row)
        row.source_provider_key = None
        row.source_external_id = None
        row.source_url = None
        row.source_last_attempt_at = None
        row.source_last_successful_sync_at = None
        row.source_last_error_code = None
        row.source_unavailable_entry_count = 0
        row.source_truncated = False
        self._touch(row, now)
        await self._session.flush()
        return _playlist(row)

    async def selections(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        expected_revision: int | None = None,
    ) -> tuple[PlaylistTrackSelection, ...]:
        await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset(PlaylistAccess),
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

    async def add_collaborator(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        collaborator_id: UserId,
        *,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER}),
            expected_revision=expected_revision,
            for_update=True,
        )
        if row.owner_id == collaborator_id:
            raise LibraryError(LibraryErrorCode.PLAYLIST_COLLABORATOR_INVALID, 422)
        existing = await self._collaborator(
            playlist_id, collaborator_id, for_update=True
        )
        if existing is not None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_COLLABORATOR_EXISTS, 409)
        self._session.add(
            _PlaylistCollaboratorRow(
                playlist_id=playlist_id,
                user_id=collaborator_id,
                granted_by=actor_id,
                granted_at=now,
            )
        )
        self._touch(row, now)
        await self._session.flush()
        return _playlist(row)

    async def remove_collaborator(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        collaborator_id: UserId,
        *,
        expected_revision: int,
        now: datetime,
    ) -> Playlist:
        row, _access = await self._authorized(
            actor_id,
            playlist_id,
            allowed=frozenset({PlaylistAccess.OWNER}),
            expected_revision=expected_revision,
            for_update=True,
        )
        if row.owner_id == collaborator_id:
            raise LibraryError(LibraryErrorCode.PLAYLIST_COLLABORATOR_INVALID, 422)
        existing = await self._collaborator(
            playlist_id, collaborator_id, for_update=True
        )
        if existing is None:
            return _playlist(row)
        await self._session.delete(existing)
        self._touch(row, now)
        await self._session.flush()
        return _playlist(row)

    async def _authorized(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        allowed: frozenset[PlaylistAccess],
        expected_revision: int | None = None,
        for_update: bool = False,
    ) -> tuple[_PlaylistRow, PlaylistAccess]:
        statement = select(_PlaylistRow).where(_PlaylistRow.id == playlist_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        if row is None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_NOT_FOUND, 404)
        access = await self._access(row, actor_id, for_update=for_update)
        if access is None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_NOT_FOUND, 404)
        if access not in allowed:
            raise LibraryError(LibraryErrorCode.PLAYLIST_ACCESS_DENIED, 403)
        if expected_revision is not None and row.revision != expected_revision:
            raise LibraryError(LibraryErrorCode.PLAYLIST_REVISION_CONFLICT, 409)
        return row, access

    async def _access(
        self,
        row: _PlaylistRow,
        actor_id: UserId,
        *,
        for_update: bool,
    ) -> PlaylistAccess | None:
        if row.owner_id == actor_id:
            return PlaylistAccess.OWNER
        if row.visibility is PlaylistVisibility.PRIVATE:
            return None
        collaborator = await self._collaborator(
            PlaylistId(row.id), actor_id, for_update=for_update
        )
        if collaborator is not None:
            return PlaylistAccess.EDITOR
        if row.visibility is PlaylistVisibility.PUBLIC:
            return PlaylistAccess.READER
        return None

    async def _collaborator(
        self,
        playlist_id: PlaylistId,
        user_id: UserId,
        *,
        for_update: bool,
    ) -> _PlaylistCollaboratorRow | None:
        statement = select(_PlaylistCollaboratorRow).where(
            _PlaylistCollaboratorRow.playlist_id == playlist_id,
            _PlaylistCollaboratorRow.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        collaborator: _PlaylistCollaboratorRow | None = await self._session.scalar(
            statement
        )
        return collaborator

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

    async def _next_owner_position(self, owner_id: UserId) -> int:
        value = await self._session.scalar(
            select(func.max(_PlaylistRow.owner_position)).where(
                _PlaylistRow.owner_id == owner_id
            )
        )
        return 0 if value is None else int(value) + 1

    async def _owned_playlist_rows(
        self,
        owner_id: UserId,
        *,
        for_update: bool = False,
    ) -> tuple[_PlaylistRow, ...]:
        statement = (
            select(_PlaylistRow)
            .where(_PlaylistRow.owner_id == owner_id)
            .order_by(_PlaylistRow.owner_position, _PlaylistRow.id)
        )
        if for_update:
            statement = statement.with_for_update()
        return tuple((await self._session.scalars(statement)).all())

    async def _reposition_playlists(self, playlists: list[_PlaylistRow]) -> None:
        if not playlists:
            return
        offset = len(playlists) + 1
        for playlist in playlists:
            playlist.owner_position += offset
        await self._session.flush()
        for position, playlist in enumerate(playlists):
            playlist.owner_position = position
        await self._session.flush()

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

    @staticmethod
    def _require_internal(row: _PlaylistRow) -> None:
        if row.source_provider_key is not None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_LINKED_READ_ONLY, 409)


def _reaction(row: _TrackReactionRow) -> TrackReaction:
    return TrackReaction(
        UserId(row.user_id),
        TrackId(row.track_id),
        row.value,
        row.created_at,
        row.updated_at,
    )


def _entry_match_key(entry: _PlaylistEntryRow) -> tuple[bool, UUID]:
    if entry.preferred_source_id is not None:
        return True, entry.preferred_source_id
    return False, entry.track_id


def _selection_match_key(selection: PlaylistTrackSelection) -> tuple[bool, UUID]:
    if selection.preferred_source_id is not None:
        return True, selection.preferred_source_id
    return False, selection.track_id


def _playlist(row: _PlaylistRow) -> Playlist:
    source = (
        PlaylistSource(
            row.source_provider_key,
            row.source_external_id,
            row.source_url,
            row.source_last_attempt_at,
            row.source_last_successful_sync_at,
            row.source_last_error_code,
            row.source_unavailable_entry_count,
            row.source_truncated,
        )
        if row.source_provider_key is not None
        and row.source_external_id is not None
        and row.source_url is not None
        and row.source_last_attempt_at is not None
        and row.source_last_successful_sync_at is not None
        else None
    )
    return Playlist(
        PlaylistId(row.id),
        UserId(row.owner_id),
        row.owner_position,
        row.name,
        row.visibility,
        source,
        row.revision,
        row.created_at,
        row.updated_at,
    )


def _playlist_entry_undo(row: _PlaylistEntryUndoRow) -> PlaylistEntryUndo:
    return PlaylistEntryUndo(
        PlaylistEntryUndoId(row.id),
        UserId(row.actor_id),
        PlaylistEntry(
            PlaylistEntryId(row.entry_id),
            PlaylistId(row.playlist_id),
            TrackId(row.track_id),
            TrackSourceId(row.preferred_source_id)
            if row.preferred_source_id is not None
            else None,
            UserId(row.added_by),
            row.position,
            row.entry_created_at,
        ),
        row.removed_at,
        row.expires_at,
    )
