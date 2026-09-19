# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""SQLAlchemy persistence for one player's queue and playback state."""

from __future__ import annotations

import sqlite3
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
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import ConnectionPoolEntry

from .models import PlaybackState, PlayerSnapshot, QueueEntry

_SCHEMA_VERSION = 1


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


class _PlayerRow(_Base):
    __tablename__ = "player_state"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str]
    current_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("queue_entries.id")
    )


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

                if version != _SCHEMA_VERSION or tables != set(_Base.metadata.tables):
                    raise ValueError(f"Unsupported player database schema ({version}).")
                for table in _Base.metadata.sorted_tables:
                    columns = [
                        column["name"] for column in inspector.get_columns(table.name)
                    ]
                    if columns != list(table.columns.keys()):
                        raise ValueError(f"Unexpected columns in {table.name}.")
        except (SQLAlchemyError, ValueError) as exc:
            raise StorageError(f"Invalid player database: {exc}") from exc

    def load(self) -> PlayerSnapshot:
        """Read stored state as immutable domain values; do not recover playback."""
        try:
            with self._session() as session:
                players = session.scalars(select(_PlayerRow)).all()
                if len(players) != 1 or players[0].id != 1:
                    raise ValueError("Expected exactly one player state.")
                player = players[0]
                state = PlaybackState(player.state)
                rows = session.scalars(
                    select(_QueueEntryRow).order_by(_QueueEntryRow.position)
                )
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
                        )
                    )

                current = None
                if player.current_entry_id is not None:
                    if not entries or entries[0].id != player.current_entry_id:
                        raise ValueError(
                            "Current entry must precede the upcoming queue."
                        )
                    current = entries.pop(0)
                return PlayerSnapshot(state, current, tuple(entries))
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise StorageError(f"Invalid player database: {exc}") from exc

    def save(self, snapshot: PlayerSnapshot) -> None:
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
                    )
                    for position, entry in enumerate(entries)
                )
                session.flush()
                player.state = snapshot.state.value
                player.current_entry_id = (
                    snapshot.current.id if snapshot.current else None
                )
        except SQLAlchemyError as exc:
            raise StorageError(f"Cannot save player state: {exc}") from exc
