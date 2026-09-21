# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""SQLAlchemy storage for the new core in caller-owned transactions."""

import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from sqlalchemy import JSON, URL, CheckConstraint, Connection, DateTime, ForeignKey
from sqlalchemy import (
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    delete,
    event,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    composite,
    mapped_column,
    relationship,
)
from sqlalchemy.pool import ConnectionPoolEntry
from sqlalchemy.types import TypeDecorator

from .domain.metadata import (
    MetadataKind,
    MetadataProvenance,
    MetadataSource,
    TrackMetadata,
)
from nahormaar_backend.domain.identity import Contributor
from .domain.queue import Outcome, QueueEntry, QueueOrigin, Removal
from .domain.sessions import (
    ListeningSession,
    PlaybackCheckpoint,
    PlaybackEndReason,
    PlaybackIntent,
    PlaybackRecord,
    Receipt,
)
from .domain.tracks import Artist, ArtistIdentity, MediaIdentity, Track


def database_engine(path: Path) -> AsyncEngine:
    """Create a lazy connection pool with foreign keys and explicit transactions.

    The owner supplies the path, manages schema migrations and disposes the pool.
    The write unit of work reserves SQLite's writer before any read/write upgrade.
    Ordinary read transactions can still inspect the last committed data while
    a writer is active. PRAGMA foreign_keys runs before either transaction type.
    """
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(path)),
        connect_args={
            "autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL,
            "isolation_level": None,
        },
    )

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection: DBAPIConnection, record: ConnectionPoolEntry) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection: Connection) -> None:
        connection.exec_driver_sql(
            "BEGIN IMMEDIATE"
            if connection.get_execution_options().get("sqlite_write")
            else "BEGIN"
        )

    return engine


@asynccontextmanager
async def write_transaction(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """Reserve before reading so independent short mutations cannot deadlock.

    Provider/audio waits belong outside. Repositories only flush; this application
    boundary commits or rolls back all state and operation evidence together.
    """
    async with sessions.begin() as session:
        await session.connection(execution_options={"sqlite_write": True})
        yield session


class Base(DeclarativeBase):
    """Schema metadata for the new core, separate from the running application."""


class _DomainJSON[T](TypeDecorator[T]):
    """Typed immutable operation evidence, not a second metadata schema."""

    impl = JSON
    cache_ok = True

    def __init__(self, value_type: type[T]) -> None:
        super().__init__()
        self.value_type = value_type
        self._adapter = TypeAdapter(value_type)

    def process_bind_param(self, value: T | None, dialect: Dialect) -> object:
        return None if value is None else self._adapter.dump_python(value, mode="json")

    def process_result_value(self, value: object, dialect: Dialect) -> T | None:
        return None if value is None else self._adapter.validate_python(value)


class _ContributorJSON(TypeDecorator[Contributor]):
    """Preserve the account/display snapshot consistently across stored occurrences."""

    impl = JSON
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(none_as_null=True)

    def process_bind_param(
        self, value: Contributor | None, dialect: Dialect
    ) -> dict[str, str] | None:
        if value is None:
            return None
        return {"id": str(value.id), "name": value.name, "avatar": value.avatar}

    def process_result_value(
        self, value: dict[str, str] | None, dialect: Dialect
    ) -> Contributor | None:
        if value is None:
            return None
        return Contributor(UUID(value["id"]), value["name"], value["avatar"])


class _UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores UTC without an offset; domain reads remain timezone-aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("Stored timestamps must be timezone-aware.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def _source_json(source: MetadataSource) -> dict[str, str]:
    return {
        "provider": source.provider,
        "kind": source.kind.value,
        "observed_at": source.observed_at.astimezone(UTC).isoformat(),
    }


def _source_value(value: dict[str, str]) -> MetadataSource:
    return MetadataSource(
        value["provider"],
        MetadataKind(value["kind"]),
        datetime.fromisoformat(value["observed_at"]),
    )


class _MetadataSourceJSON(TypeDecorator[MetadataSource]):
    impl = JSON
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(none_as_null=True)

    def process_bind_param(
        self, value: MetadataSource | None, dialect: Dialect
    ) -> dict[str, str] | None:
        return _source_json(value) if value is not None else None

    def process_result_value(
        self, value: dict[str, str] | None, dialect: Dialect
    ) -> MetadataSource | None:
        return _source_value(value) if value is not None else None


class _MetadataProvenanceJSON(TypeDecorator[MetadataProvenance]):
    impl = JSON
    cache_ok = True

    def process_bind_param(
        self, value: MetadataProvenance | None, dialect: Dialect
    ) -> dict[str, dict[str, str]]:
        if value is None:
            return {}
        return {
            attribute.name: _source_json(source)
            for attribute in fields(value)
            if (source := getattr(value, attribute.name)) is not None
        }

    def process_result_value(
        self, value: dict[str, dict[str, str]] | None, dialect: Dialect
    ) -> MetadataProvenance:
        return MetadataProvenance(
            **{name: _source_value(source) for name, source in (value or {}).items()}
        )


class _ListeningSessionRow(Base):
    __tablename__ = "listening_sessions"
    __table_args__ = (
        CheckConstraint("channel_id > 0", name="ck_session_channel"),
        CheckConstraint("volume >= 0 AND volume <= 1", name="ck_session_volume"),
        CheckConstraint(
            "crossfade_seconds IN (0,3,4,5,6,7)", name="ck_session_crossfade"
        ),
        CheckConstraint(
            "queue_revision >= 0 AND revision >= queue_revision",
            name="ck_session_revisions",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    channel_id: Mapped[int | None]
    volume: Mapped[float]
    revision: Mapped[int] = mapped_column(default=0)
    queue_revision: Mapped[int] = mapped_column(default=0)
    crossfade_seconds: Mapped[int] = mapped_column(default=0)

    def to_session(self) -> ListeningSession:
        return ListeningSession(
            self.id,
            self.channel_id,
            self.volume,
            self.revision,
            self.queue_revision,
            self.crossfade_seconds,
        )


class ListeningSessionRepository:
    """Persist ownership and settings without constructing a running Session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: UUID) -> ListeningSession | None:
        row = await self._session.get(_ListeningSessionRow, session_id)
        return row.to_session() if row is not None else None

    async def add(self, value: ListeningSession) -> None:
        self._session.add(
            _ListeningSessionRow(
                id=value.id,
                channel_id=value.channel_id,
                volume=value.volume,
                revision=value.revision,
                queue_revision=value.queue_revision,
                crossfade_seconds=value.crossfade_seconds,
            )
        )
        await self._session.flush()

    async def update(
        self, value: ListeningSession, *, expected_revision: int | None = None
    ) -> None:
        if expected_revision is not None:
            changed = await self._session.scalar(
                update(_ListeningSessionRow)
                .where(
                    _ListeningSessionRow.id == value.id,
                    _ListeningSessionRow.revision == expected_revision,
                )
                .values(
                    channel_id=value.channel_id,
                    volume=value.volume,
                    revision=value.revision,
                    queue_revision=value.queue_revision,
                    crossfade_seconds=value.crossfade_seconds,
                )
                .returning(_ListeningSessionRow.id)
            )
            if changed is None:
                raise RuntimeError("Session changed outside its owning command loop.")
            return
        row = await self._session.get(_ListeningSessionRow, value.id)
        if row is None:
            raise LookupError(f"Unknown listening session: {value.id}")
        row.channel_id = value.channel_id
        row.volume = value.volume
        row.crossfade_seconds = value.crossfade_seconds
        row.revision, row.queue_revision = value.revision, value.queue_revision
        await self._session.flush()


class _ArtistRow(Base):
    __tablename__ = "artists"
    __table_args__ = (
        UniqueConstraint(
            "namespace", "external_id", name="uq_artists_provider_identity"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    identity: Mapped[ArtistIdentity] = composite(
        mapped_column("namespace"), mapped_column("external_id")
    )
    name: Mapped[str]
    name_source: Mapped[MetadataSource | None] = mapped_column(_MetadataSourceJSON())

    def to_artist(self) -> Artist:
        return Artist(
            id=self.id,
            identity=self.identity,
            name=self.name,
            name_source=self.name_source,
        )


class ArtistRepository:
    """Resolve artists by provider identity; names are descriptive, not unique."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, artist_id: UUID) -> Artist | None:
        row = await self._session.get(_ArtistRow, artist_id)
        return row.to_artist() if row is not None else None

    async def find(self, identity: ArtistIdentity) -> Artist | None:
        row = await self._session.scalar(
            select(_ArtistRow).where(_ArtistRow.identity == identity)
        )
        return row.to_artist() if row is not None else None

    async def add(self, artist: Artist) -> None:
        self._session.add(
            _ArtistRow(
                id=artist.id,
                identity=artist.identity,
                name=artist.name,
                name_source=artist.name_source,
            )
        )
        await self._session.flush()

    async def update(self, artist: Artist) -> None:
        row = await self._session.get(_ArtistRow, artist.id)
        if row is None:
            raise LookupError(f"Unknown artist: {artist.id}")
        if row.identity != artist.identity:
            raise ValueError("An existing artist's provider identity cannot change.")
        row.name = artist.name
        row.name_source = artist.name_source
        await self._session.flush()


class _TrackRow(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("namespace", "external_id", name="uq_tracks_media_identity"),
        CheckConstraint("duration_seconds >= 0", name="ck_tracks_duration"),
        CheckConstraint("updated_at >= created_at", name="ck_tracks_timestamps"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_url: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(_UTCDateTime())
    checked_at: Mapped[datetime | None] = mapped_column(_UTCDateTime())
    provenance: Mapped[MetadataProvenance] = mapped_column(
        _MetadataProvenanceJSON(), default=MetadataProvenance
    )
    identity: Mapped[MediaIdentity] = composite(
        mapped_column("namespace"), mapped_column("external_id")
    )
    details: Mapped[TrackMetadata] = composite(
        mapped_column("title"),
        mapped_column("artist"),
        mapped_column("uploader"),
        mapped_column("uploader_url"),
        mapped_column("duration_seconds"),
        mapped_column("thumbnail_url"),
    )
    credits: Mapped[list["_TrackArtistRow"]] = relationship(
        order_by="_TrackArtistRow.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_track(self) -> Track:
        return Track(
            id=self.id,
            identity=self.identity,
            source_url=self.source_url,
            metadata=self.details,
            created_at=self.created_at,
            updated_at=self.updated_at,
            checked_at=self.checked_at,
            artist_ids=tuple(credit.artist_id for credit in self.credits),
            provenance=self.provenance,
        )


class _TrackArtistRow(Base):
    __tablename__ = "track_artists"
    __table_args__ = (
        UniqueConstraint("track_id", "position", name="uq_track_artist_position"),
        CheckConstraint("position >= 0", name="ck_track_artist_position"),
    )

    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )
    artist_id: Mapped[UUID] = mapped_column(
        ForeignKey("artists.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    position: Mapped[int]


class TrackRepository:
    """Persist complete domain values without choosing metadata merge policy.

    The caller owns the AsyncSession and its transaction. Writes flush so that
    constraint errors surface here, but never commit, roll back or close it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, track_id: UUID) -> Track | None:
        row = await self._session.get(_TrackRow, track_id)
        return row.to_track() if row is not None else None

    async def get_many(
        self, identifiers: tuple[UUID | MediaIdentity, ...]
    ) -> tuple[Track, ...]:
        rows = await self._session.scalars(
            select(_TrackRow).where(
                or_(
                    _TrackRow.id.in_(
                        value for value in identifiers if isinstance(value, UUID)
                    ),
                    *(
                        _TrackRow.identity == value
                        for value in identifiers
                        if isinstance(value, MediaIdentity)
                    ),
                )
            )
        )
        return tuple(row.to_track() for row in rows)

    async def find(self, identity: MediaIdentity) -> Track | None:
        row = await self._session.scalar(
            select(_TrackRow).where(_TrackRow.identity == identity)
        )
        return row.to_track() if row is not None else None

    async def add(self, track: Track) -> None:
        self._session.add(
            _TrackRow(
                id=track.id,
                identity=track.identity,
                source_url=track.source_url,
                details=track.metadata,
                created_at=track.created_at,
                updated_at=track.updated_at,
                checked_at=track.checked_at,
                provenance=track.provenance,
                credits=[
                    _TrackArtistRow(artist_id=artist_id, position=position)
                    for position, artist_id in enumerate(track.artist_ids)
                ],
            )
        )
        await self._session.flush()

    async def update(self, track: Track) -> None:
        row = await self._session.get(_TrackRow, track.id)
        if row is None:
            raise LookupError(f"Unknown track: {track.id}")
        if row.identity != track.identity:
            raise ValueError("An existing track's media identity cannot change.")
        if row.created_at != track.created_at:
            raise ValueError("An existing track's creation time cannot change.")
        row.source_url = track.source_url
        row.details = track.metadata
        row.updated_at = track.updated_at
        row.checked_at = track.checked_at
        row.provenance = track.provenance
        if tuple(credit.artist_id for credit in row.credits) != track.artist_ids:
            # Delete old positions before inserting the new order within this transaction.
            row.credits.clear()
            await self._session.flush()
            row.credits.extend(
                _TrackArtistRow(artist_id=artist_id, position=position)
                for position, artist_id in enumerate(track.artist_ids)
            )
        await self._session.flush()


class _QueueEntryRow(Base):
    __tablename__ = "queue_entries"
    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_queue_session_position"),
        CheckConstraint("position >= 0", name="ck_queue_position"),
        CheckConstraint("origin IN ('manual', 'radio')", name="ck_queue_origin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT")
    )
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int]
    added_by: Mapped[Contributor | None] = mapped_column(_ContributorJSON())
    origin: Mapped[str]

    def to_entry(self) -> QueueEntry:
        return QueueEntry(
            id=self.id,
            session_id=self.session_id,
            track_id=self.track_id,
            position=self.position,
            origin=QueueOrigin(self.origin),
            added_by=self.added_by,
        )


class QueueRepository:
    """Store individual occurrences; queue policy and transactions belong outside.

    Removing an entry leaves other positions unchanged. Reordering, compaction
    and undo are queue operations, not side effects of persistence.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: UUID, entry_id: UUID) -> QueueEntry | None:
        row = await self._session.get(_QueueEntryRow, entry_id)
        return (
            row.to_entry() if row is not None and row.session_id == session_id else None
        )

    async def entries(self, session_id: UUID) -> tuple[QueueEntry, ...]:
        rows = await self._session.scalars(
            select(_QueueEntryRow)
            .where(_QueueEntryRow.session_id == session_id)
            .order_by(_QueueEntryRow.position)
        )
        return tuple(row.to_entry() for row in rows)

    async def add(self, entry: QueueEntry) -> None:
        self._session.add(
            _QueueEntryRow(
                id=entry.id,
                session_id=entry.session_id,
                track_id=entry.track_id,
                position=entry.position,
                origin=entry.origin.value,
                added_by=entry.added_by,
            )
        )
        await self._session.flush()

    async def remove(self, session_id: UUID, entry_id: UUID) -> bool:
        removed = await self._session.scalar(
            delete(_QueueEntryRow)
            .where(
                _QueueEntryRow.session_id == session_id,
                _QueueEntryRow.id == entry_id,
            )
            .returning(_QueueEntryRow.id)
        )
        return removed is not None

    async def replace(self, session_id: UUID, entries: tuple[QueueEntry, ...]) -> None:
        if any(
            entry.session_id != session_id or entry.position != index
            for index, entry in enumerate(entries)
        ):
            raise ValueError(
                "Stored queue must have contiguous positions within its session."
            )
        await self._session.execute(
            delete(_QueueEntryRow).where(_QueueEntryRow.session_id == session_id)
        )
        self._session.add_all(
            [
                _QueueEntryRow(
                    id=entry.id,
                    session_id=session_id,
                    track_id=entry.track_id,
                    position=entry.position,
                    origin=entry.origin.value,
                    added_by=entry.added_by,
                )
                for entry in entries
            ]
        )
        await self._session.flush()


class _ReceiptRow(Base):
    __tablename__ = "operation_receipts"
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT"), index=True
    )
    fingerprint: Mapped[str]
    actor_id: Mapped[UUID | None]
    outcome: Mapped[Outcome] = mapped_column(_DomainJSON(Outcome))
    # Retain the nullable column from the applied engine_0001 schema. No writer uses it.
    imported_outcome: Mapped[dict[str, object] | None] = mapped_column(JSON)


class _RemovalRow(Base):
    __tablename__ = "queue_removals"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT")
    )
    expires_at: Mapped[datetime] = mapped_column(_UTCDateTime(), index=True)
    removal: Mapped[Removal] = mapped_column(_DomainJSON(Removal))


class OperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, request_id: UUID) -> Receipt | None:
        row = await self._session.get(_ReceiptRow, request_id)
        return (
            Receipt(
                row.request_id,
                row.session_id,
                row.fingerprint,
                row.actor_id,
                row.outcome,
            )
            if row
            else None
        )

    async def add(
        self,
        receipt: Receipt,
        *,
        removal: Removal | None = None,
        consume: UUID | None = None,
    ) -> None:
        self._session.add(
            _ReceiptRow(
                request_id=receipt.request_id,
                session_id=receipt.session_id,
                fingerprint=receipt.fingerprint,
                actor_id=receipt.actor_id,
                outcome=receipt.outcome,
            )
        )
        if removal is not None:
            self._session.add(
                _RemovalRow(
                    id=removal.id,
                    session_id=receipt.session_id,
                    expires_at=removal.expires_at,
                    removal=removal,
                )
            )
        if consume is not None:
            await self._session.execute(
                delete(_RemovalRow).where(
                    _RemovalRow.id == consume,
                    _RemovalRow.session_id == receipt.session_id,
                )
            )
        await self._session.flush()

    async def removal(
        self, session_id: UUID, removal_id: UUID, *, now: datetime
    ) -> Removal | None:
        await self._session.execute(
            delete(_RemovalRow).where(_RemovalRow.expires_at <= now)
        )
        row = await self._session.get(_RemovalRow, removal_id)
        return row.removal if row and row.session_id == session_id else None


class _PlaybackRecordRow(Base):
    __tablename__ = "playback_records"
    __table_args__ = (
        UniqueConstraint("id", "session_id", "entry_id", "track_id"),
        Index("ix_playback_session_started", "session_id", "started_at", "id"),
        CheckConstraint("origin IN ('manual', 'radio')", name="ck_playback_origin"),
        CheckConstraint(
            "(ended_at IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND end_reason IS NOT NULL AND "
            "ended_at >= started_at AND "
            "end_reason IN ('completed', 'skipped', 'stopped', 'failed'))",
            name="ck_playback_end",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT")
    )
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), index=True
    )
    entry_id: Mapped[UUID]
    started_at: Mapped[datetime] = mapped_column(_UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(_UTCDateTime())
    end_reason: Mapped[str | None]
    added_by: Mapped[Contributor | None] = mapped_column(_ContributorJSON())
    origin: Mapped[str]

    def to_record(self) -> PlaybackRecord:
        return PlaybackRecord(
            id=self.id,
            session_id=self.session_id,
            track_id=self.track_id,
            entry_id=self.entry_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            end_reason=(
                PlaybackEndReason(self.end_reason)
                if self.end_reason is not None
                else None
            ),
            added_by=self.added_by,
            origin=QueueOrigin(self.origin),
        )


class PlaybackRecordRepository:
    """Store confirmed plays; choosing when they start/end belongs to playback."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: UUID, play_id: UUID) -> PlaybackRecord | None:
        row = await self._session.get(_PlaybackRecordRow, play_id)
        return (
            row.to_record()
            if row is not None and row.session_id == session_id
            else None
        )

    async def recent(
        self, session_id: UUID, *, limit: int = 100
    ) -> tuple[PlaybackRecord, ...]:
        if type(limit) is not int or limit <= 0:
            raise ValueError("History limit must be a positive integer.")
        rows = await self._session.scalars(
            select(_PlaybackRecordRow)
            .where(_PlaybackRecordRow.session_id == session_id)
            .order_by(
                _PlaybackRecordRow.started_at.desc(), _PlaybackRecordRow.id.desc()
            )
            .limit(limit)
        )
        return tuple(row.to_record() for row in rows)

    async def add(self, record: PlaybackRecord) -> None:
        self._session.add(
            _PlaybackRecordRow(
                id=record.id,
                session_id=record.session_id,
                track_id=record.track_id,
                entry_id=record.entry_id,
                started_at=record.started_at,
                ended_at=record.ended_at,
                end_reason=record.end_reason.value if record.end_reason else None,
                added_by=record.added_by,
                origin=record.origin.value,
            )
        )
        await self._session.flush()

    async def update(self, record: PlaybackRecord) -> None:
        row = await self._session.get(_PlaybackRecordRow, record.id)
        if row is None or row.session_id != record.session_id:
            raise LookupError(f"Unknown playback record: {record.id}")
        original = row.to_record()
        if (
            replace(record, ended_at=original.ended_at, end_reason=original.end_reason)
            != original
        ):
            raise ValueError(
                "A playback record's identity and start context cannot change."
            )
        if original.ended_at is not None and record != original:
            raise ValueError("A finished playback record cannot change.")
        row.ended_at = record.ended_at
        row.end_reason = record.end_reason.value if record.end_reason else None
        await self._session.flush()


class _PlaybackCheckpointRow(Base):
    __tablename__ = "playback_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["play_id", "session_id", "entry_id", "track_id"],
            [
                "playback_records.id",
                "playback_records.session_id",
                "playback_records.entry_id",
                "playback_records.track_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("position_seconds >= 0", name="ck_checkpoint_position"),
        CheckConstraint("origin IN ('manual', 'radio')", name="ck_checkpoint_origin"),
        CheckConstraint(
            "(intent = 'stopped' AND entry_id IS NULL AND track_id IS NULL AND "
            "play_id IS NULL AND position_seconds = 0 AND added_by IS NULL AND "
            "origin = 'manual') OR (intent IN ('playing', 'paused') AND "
            "entry_id IS NOT NULL AND track_id IS NOT NULL)",
            name="ck_checkpoint_intent",
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT"), primary_key=True
    )
    track_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), index=True
    )
    entry_id: Mapped[UUID | None]
    play_id: Mapped[UUID | None]
    intent: Mapped[str]
    position_seconds: Mapped[float]
    added_by: Mapped[Contributor | None] = mapped_column(_ContributorJSON())
    origin: Mapped[str]

    def to_checkpoint(self) -> PlaybackCheckpoint:
        return PlaybackCheckpoint(
            session_id=self.session_id,
            intent=PlaybackIntent(self.intent),
            entry_id=self.entry_id,
            track_id=self.track_id,
            play_id=self.play_id,
            position_seconds=self.position_seconds,
            added_by=self.added_by,
            origin=QueueOrigin(self.origin),
        )


class PlaybackCheckpointRepository:
    """One recovery checkpoint per session, saved in the enclosing transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: UUID) -> PlaybackCheckpoint | None:
        row = await self._session.get(_PlaybackCheckpointRow, session_id)
        return row.to_checkpoint() if row is not None else None

    async def save(self, checkpoint: PlaybackCheckpoint) -> None:
        await self._session.merge(
            _PlaybackCheckpointRow(
                session_id=checkpoint.session_id,
                track_id=checkpoint.track_id,
                entry_id=checkpoint.entry_id,
                play_id=checkpoint.play_id,
                intent=checkpoint.intent.value,
                position_seconds=checkpoint.position_seconds,
                added_by=checkpoint.added_by,
                origin=checkpoint.origin.value,
            )
        )
        await self._session.flush()

    async def clear(self, session_id: UUID) -> None:
        await self._session.execute(
            delete(_PlaybackCheckpointRow).where(
                _PlaybackCheckpointRow.session_id == session_id
            )
        )
