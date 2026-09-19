# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""SQLAlchemy persistence for one player's queue and playback state."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from types import TracebackType
from uuid import UUID

from sqlalchemy import (
    URL,
    CheckConstraint,
    ForeignKey,
    create_engine,
    delete,
    event,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import ConnectionPoolEntry

from .commands import Outcome, Receipt, Revisions
from .models import HistoryEntry, PlaybackState, PlayerSnapshot, QueueEntry

_SCHEMA_VERSION = 3


class _Base(DeclarativeBase):
    pass


class _QueueEntryRow(_Base):
    __tablename__ = "queue_entries"
    __table_args__ = (CheckConstraint("position >= 0"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(unique=True)
    source_url: Mapped[str]
    video_id: Mapped[str | None]
    title: Mapped[str | None]
    uploader: Mapped[str | None]
    duration_seconds: Mapped[float | None]
    thumbnail_url: Mapped[str | None]
    artist: Mapped[str | None]
    uploader_url: Mapped[str | None]


class _HistoryRow(_Base):
    __tablename__ = "playback_history"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(unique=True)
    played_at: Mapped[str]
    entry_id: Mapped[UUID]
    source_url: Mapped[str]
    video_id: Mapped[str | None]
    title: Mapped[str | None]
    uploader: Mapped[str | None]
    duration_seconds: Mapped[float | None]
    thumbnail_url: Mapped[str | None]
    artist: Mapped[str | None]
    uploader_url: Mapped[str | None]


class _PlayerRow(_Base):
    __tablename__ = "player_state"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str]
    current_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("queue_entries.id")
    )
    revision: Mapped[int] = mapped_column(default=0, server_default="0")
    queue_revision: Mapped[int] = mapped_column(default=0, server_default="0")


class _RequestRow(_Base):
    __tablename__ = "requests"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    fingerprint: Mapped[str]
    code: Mapped[str | None]
    status_code: Mapped[int | None]
    entry_id: Mapped[UUID | None]


class StorageError(RuntimeError):
    """The player database could not be opened, read or committed."""


def _enable_foreign_keys(
    connection: sqlite3.Connection, record: ConnectionPoolEntry
) -> None:
    # SQLite ignores this PRAGMA inside a transaction.
    connection.autocommit = True
    try:
        cursor = connection.execute("PRAGMA foreign_keys = ON")
        cursor.close()
    finally:
        connection.autocommit = False


class SQLiteStore:
    """Own a SQLAlchemy engine for one synchronous Player."""

    def __init__(self, path: Path, *, timeout: float = 5.0) -> None:
        self._engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(path)),
            connect_args={"autocommit": False, "timeout": timeout},
        )
        event.listen(self._engine, "connect", _enable_foreign_keys)
        self._closed = False
        try:
            self._prepare_schema()
            self.load()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True
        self._engine.dispose()

    def _session(self) -> Session:
        if self._closed:
            raise StorageError("Player database is closed.")
        return Session(self._engine)

    def _prepare_schema(self) -> None:
        try:
            with self._engine.begin() as connection:
                version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
                inspector = inspect(connection)
                tables = set(inspector.get_table_names())
                if version == 0 and not tables and not inspector.get_view_names():
                    _Base.metadata.create_all(connection)
                    connection.execute(
                        insert(_PlayerRow).values(id=1, state=PlaybackState.IDLE.value)
                    )
                    connection.exec_driver_sql(
                        f"PRAGMA user_version = {_SCHEMA_VERSION}"
                    )
                    return

                expected_tables = set(_Base.metadata.tables)
                if version == 1:
                    expected_tables.remove("requests")
                if version in (1, 2):
                    expected_tables.remove("playback_history")
                if version not in (1, 2, _SCHEMA_VERSION) or tables != expected_tables:
                    raise ValueError(f"Unsupported player database schema ({version}).")
                for table in _Base.metadata.sorted_tables:
                    if table.name not in expected_tables:
                        continue
                    columns = [
                        column["name"] for column in inspector.get_columns(table.name)
                    ]
                    expected_columns = list(table.columns.keys())
                    if version == 1 and table.name == "player_state":
                        expected_columns = expected_columns[:-2]
                    if version in (1, 2) and table.name == "queue_entries":
                        expected_columns = expected_columns[:-2]
                    if columns != expected_columns:
                        raise ValueError(f"Unexpected columns in {table.name}.")
                if inspector.get_view_names():
                    raise ValueError("Unexpected database views.")
                if version == 1:
                    for name in ("revision", "queue_revision"):
                        connection.exec_driver_sql(
                            f"ALTER TABLE player_state ADD COLUMN {name} "
                            "INTEGER NOT NULL DEFAULT 0"
                        )
                    _Base.metadata.tables["requests"].create(connection)
                if version in (1, 2):
                    for name in ("artist", "uploader_url"):
                        connection.exec_driver_sql(
                            f"ALTER TABLE queue_entries ADD COLUMN {name} VARCHAR"
                        )
                    _Base.metadata.tables["playback_history"].create(connection)
                    # Validate old data before committing any schema changes.
                    with Session(bind=connection) as session:
                        self._load(session)
                    connection.exec_driver_sql("PRAGMA user_version = 3")
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise StorageError(f"Invalid player database: {exc}") from exc

    def load(self) -> PlayerSnapshot:
        """Read stored state as immutable domain values; do not recover playback."""
        try:
            with self._session() as session:
                return self._load(session)
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise StorageError(f"Invalid player database: {exc}") from exc

    def _load(self, session: Session) -> PlayerSnapshot:
        players = session.scalars(select(_PlayerRow)).all()
        if len(players) != 1 or players[0].id != 1:
            raise ValueError("Expected exactly one player state.")
        player = players[0]
        Revisions(player.revision, player.queue_revision)
        state = PlaybackState(player.state)
        rows = session.scalars(select(_QueueEntryRow).order_by(_QueueEntryRow.position))
        entries: list[QueueEntry] = []
        for position, row in enumerate(rows):
            if row.position != position:
                raise ValueError("Queue positions must be contiguous integers.")
            entries.append(
                QueueEntry(
                    id=row.id,
                    source_url=row.source_url,
                    video_id=row.video_id,
                    title=row.title,
                    uploader=row.uploader,
                    duration_seconds=row.duration_seconds,
                    thumbnail_url=row.thumbnail_url,
                    artist=row.artist,
                    uploader_url=row.uploader_url,
                )
            )

        current = None
        if player.current_entry_id is not None:
            if not entries or entries[0].id != player.current_entry_id:
                raise ValueError("Current entry must precede the upcoming queue.")
            current = entries.pop(0)
        history: list[HistoryEntry] = []
        for position, item in enumerate(
            session.scalars(select(_HistoryRow).order_by(_HistoryRow.position))
        ):
            if item.position != position:
                raise ValueError("History positions must be contiguous integers.")
            history.append(
                HistoryEntry(
                    id=item.id,
                    played_at=datetime.fromisoformat(item.played_at),
                    entry=QueueEntry(
                        id=item.entry_id,
                        source_url=item.source_url,
                        video_id=item.video_id,
                        title=item.title,
                        uploader=item.uploader,
                        duration_seconds=item.duration_seconds,
                        thumbnail_url=item.thumbnail_url,
                        artist=item.artist,
                        uploader_url=item.uploader_url,
                    ),
                )
            )
        return PlayerSnapshot(
            state, current, tuple(entries), recently_played=tuple(history)
        )

    def save(
        self,
        snapshot: PlayerSnapshot,
        *,
        revisions: Revisions | None = None,
        receipt: Receipt | None = None,
    ) -> None:
        """Commit a whole snapshot, or leave the previous snapshot intact."""
        entries = snapshot.upcoming
        if snapshot.current is not None:
            entries = (snapshot.current, *entries)
        try:
            with self._session() as session, session.begin():
                player = session.get(_PlayerRow, 1)
                if player is None:
                    raise StorageError("Player state is missing.")
                player.state = PlaybackState.IDLE.value
                player.current_entry_id = None
                session.flush()
                session.execute(delete(_QueueEntryRow))
                session.add_all(
                    _QueueEntryRow(
                        id=entry.id,
                        position=position,
                        source_url=entry.source_url,
                        video_id=entry.video_id,
                        title=entry.title,
                        uploader=entry.uploader,
                        duration_seconds=entry.duration_seconds,
                        thumbnail_url=entry.thumbnail_url,
                        artist=entry.artist,
                        uploader_url=entry.uploader_url,
                    )
                    for position, entry in enumerate(entries)
                )
                session.execute(delete(_HistoryRow))
                session.add_all(
                    _HistoryRow(
                        id=item.id,
                        position=position,
                        played_at=item.played_at.isoformat(),
                        entry_id=item.entry.id,
                        source_url=item.entry.source_url,
                        video_id=item.entry.video_id,
                        title=item.entry.title,
                        uploader=item.entry.uploader,
                        duration_seconds=item.entry.duration_seconds,
                        thumbnail_url=item.entry.thumbnail_url,
                        artist=item.entry.artist,
                        uploader_url=item.entry.uploader_url,
                    )
                    for position, item in enumerate(snapshot.recently_played)
                )
                session.flush()
                player.state = snapshot.state.value
                player.current_entry_id = (
                    snapshot.current.id if snapshot.current else None
                )
                if revisions is not None:
                    player.revision = revisions.revision
                    player.queue_revision = revisions.queue_revision
                if receipt is not None:
                    self._finish(session, receipt)
        except SQLAlchemyError as exc:
            raise StorageError(f"Cannot save player state: {exc}") from exc

    def revisions(self) -> Revisions:
        try:
            with self._session() as session:
                row = session.get(_PlayerRow, 1)
                if row is None:
                    raise StorageError("Player state is missing.")
                return Revisions(row.revision, row.queue_revision)
        except (SQLAlchemyError, ValueError) as exc:
            raise StorageError("Cannot read player revisions.") from exc

    def reserve(self, receipt: Receipt) -> Receipt | None:
        """Return an existing receipt, or commit a reservation before any effect."""
        try:
            with self._session() as session, session.begin():
                row = session.get(_RequestRow, receipt.request_id)
                if row is not None:
                    outcome = (
                        Outcome(row.code, row.status_code, row.entry_id)
                        if row.code is not None and row.status_code is not None
                        else None
                    )
                    return Receipt(row.id, row.fingerprint, outcome)
                session.add(
                    _RequestRow(id=receipt.request_id, fingerprint=receipt.fingerprint)
                )
                return None
        except SQLAlchemyError as exc:
            raise StorageError("Cannot reserve control request.") from exc

    @staticmethod
    def _finish(session: Session, receipt: Receipt) -> None:
        row = session.get(_RequestRow, receipt.request_id)
        if row is None or receipt.outcome is None:
            raise StorageError("Cannot finish an unreserved control request.")
        row.code = receipt.outcome.code
        row.status_code = receipt.outcome.status_code
        row.entry_id = receipt.outcome.entry_id

    def finish(self, receipt: Receipt) -> None:
        try:
            with self._session() as session, session.begin():
                self._finish(session, receipt)
        except SQLAlchemyError as exc:
            raise StorageError("Cannot save control request outcome.") from exc

    def interrupt_requests(self) -> None:
        try:
            with self._session() as session, session.begin():
                session.execute(
                    update(_RequestRow)
                    .where(_RequestRow.code.is_(None))
                    .values(code="interrupted", status_code=409)
                )
        except SQLAlchemyError as exc:
            raise StorageError("Cannot recover control requests.") from exc
