# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""SQLAlchemy persistence for one player's queue and playback state."""

from __future__ import annotations

from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from time import time
from types import TracebackType
from typing import cast
from uuid import UUID

from alembic.util.exc import CommandError
from pydantic import TypeAdapter
from sqlalchemy import (
    delete,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..application.recovery import reconcile_checkpoint
from ..application.storage import StorageError as StorageError
from ..domain.commands import Outcome, Receipt, Revisions
from ..domain.checkpoint import PlaybackCheckpoint
from ..domain.models import (
    Contributor,
    HistoryEntry,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
)
from ..domain.undo import Removal, UndoUnavailable
from .accounts import account_from_row
from .database import database_engine
from .migrations import upgrade
from .models import (
    AccountRow,
    HistoryRow,
    PlayerRow,
    PlaybackCheckpointRow,
    QueueEntryRow,
    RequestRow,
    UndoRow,
)

_OUTCOME = TypeAdapter(Outcome)
_REMOVAL = TypeAdapter(Removal)


class _CheckpointDefault(Enum):
    RECONCILE = auto()


def _contributor_data(contributor: Contributor | None) -> dict[str, str] | None:
    if contributor is None:
        return None
    return {
        "id": str(contributor.id),
        "name": contributor.name,
        "avatar": contributor.avatar,
    }


def _contributor(data: object) -> Contributor | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("Invalid stored contributor.")
    fields = cast(dict[object, object], data)
    identifier, name, avatar = (fields.get(key) for key in ("id", "name", "avatar"))
    if (
        set(fields) != {"id", "name", "avatar"}
        or not isinstance(identifier, str)
        or not isinstance(name, str)
        or not isinstance(avatar, str)
    ):
        raise ValueError("Invalid stored contributor.")
    return Contributor(UUID(identifier), name, avatar)


class SQLiteStore:
    """Own a SQLAlchemy engine for one synchronous Player."""

    def __init__(self, path: Path, *, timeout: float = 5.0) -> None:
        self._engine = database_engine(path, timeout)
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
                upgrade(connection)
                with Session(bind=connection) as session:
                    self._load(session)
                    self._checkpoint(session)
                    for row in session.scalars(select(AccountRow)):
                        account_from_row(row)
        except (SQLAlchemyError, CommandError, TypeError, ValueError) as exc:
            raise StorageError(f"Invalid player database: {exc}") from exc

    def load(self) -> PlayerSnapshot:
        """Read stored state as immutable domain values; do not recover playback."""
        try:
            with self._session() as session:
                return self._load(session)
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise StorageError(f"Invalid player database: {exc}") from exc

    def _load(self, session: Session) -> PlayerSnapshot:
        players = session.scalars(select(PlayerRow)).all()
        if len(players) != 1 or players[0].id != 1:
            raise ValueError("Expected exactly one player state.")
        player = players[0]
        Revisions(player.revision, player.queue_revision)
        state = PlaybackState(player.state)
        rows = session.scalars(select(QueueEntryRow).order_by(QueueEntryRow.position))
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
                    added_by=_contributor(row.added_by),
                    origin=row.origin,
                )
            )

        current = None
        if player.current_entry_id is not None:
            if not entries or entries[0].id != player.current_entry_id:
                raise ValueError("Current entry must precede the upcoming queue.")
            current = entries.pop(0)
        history: list[HistoryEntry] = []
        for position, item in enumerate(
            session.scalars(select(HistoryRow).order_by(HistoryRow.position))
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
                        added_by=_contributor(item.added_by),
                        origin=item.origin,
                    ),
                )
            )
        return PlayerSnapshot(
            state,
            current,
            tuple(entries),
            recently_played=tuple(history),
            crossfade_seconds=player.crossfade_seconds,
        )

    def save(
        self,
        snapshot: PlayerSnapshot,
        *,
        checkpoint: PlaybackCheckpoint | _CheckpointDefault | None = (
            _CheckpointDefault.RECONCILE
        ),
        revisions: Revisions | None = None,
        receipt: Receipt | None = None,
    ) -> None:
        """Commit a whole snapshot, or leave the previous snapshot intact."""
        entries = snapshot.upcoming
        if snapshot.current is not None:
            entries = (snapshot.current, *entries)
        try:
            with self._session() as session, session.begin():
                checkpoint_value = (
                    reconcile_checkpoint(snapshot, self._checkpoint(session))
                    if isinstance(checkpoint, _CheckpointDefault)
                    else checkpoint
                )
                player = session.get(PlayerRow, 1)
                if player is None:
                    raise StorageError("Player state is missing.")
                player.state = PlaybackState.IDLE.value
                player.current_entry_id = None
                session.flush()
                session.execute(delete(QueueEntryRow))
                session.add_all(
                    QueueEntryRow(
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
                        added_by=_contributor_data(entry.added_by),
                        origin=entry.origin,
                    )
                    for position, entry in enumerate(entries)
                )
                session.execute(delete(HistoryRow))
                session.add_all(
                    HistoryRow(
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
                        added_by=_contributor_data(item.entry.added_by),
                        origin=item.entry.origin,
                    )
                    for position, item in enumerate(snapshot.recently_played)
                )
                session.flush()
                player.state = snapshot.state.value
                player.crossfade_seconds = snapshot.crossfade_seconds
                player.current_entry_id = (
                    snapshot.current.id if snapshot.current else None
                )
                self._write_checkpoint(session, checkpoint_value)
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
                row = session.get(PlayerRow, 1)
                if row is None:
                    raise StorageError("Player state is missing.")
                return Revisions(row.revision, row.queue_revision)
        except (SQLAlchemyError, ValueError) as exc:
            raise StorageError("Cannot read player revisions.") from exc

    def checkpoint(self) -> PlaybackCheckpoint | None:
        try:
            with self._session() as session:
                return self._checkpoint(session)
        except (SQLAlchemyError, TypeError, ValueError) as exc:
            raise StorageError("Cannot read playback checkpoint.") from exc

    def _checkpoint(self, session: Session) -> PlaybackCheckpoint | None:
        row = session.get(PlaybackCheckpointRow, 1)
        if row is None:
            return None
        checkpoint = PlaybackCheckpoint(
            row.channel_id,
            row.entry_id,
            row.position_seconds,
            row.paused,
            row.volume,
            row.history_recorded,
        )
        player = session.get(PlayerRow, 1)
        if player is None or player.current_entry_id != checkpoint.entry_id:
            raise ValueError("Checkpoint must match the current entry.")
        return checkpoint

    def save_checkpoint(self, checkpoint: PlaybackCheckpoint | None) -> None:
        try:
            with self._session() as session, session.begin():
                self._write_checkpoint(session, checkpoint)
        except (SQLAlchemyError, ValueError) as exc:
            raise StorageError("Cannot save playback checkpoint.") from exc

    @staticmethod
    def _write_checkpoint(
        session: Session, checkpoint: PlaybackCheckpoint | None
    ) -> None:
        session.execute(delete(PlaybackCheckpointRow))
        if checkpoint is not None:
            player = session.get(PlayerRow, 1)
            if player is None or player.current_entry_id != checkpoint.entry_id:
                raise ValueError("Checkpoint must match the current entry.")
            session.add(
                PlaybackCheckpointRow(
                    id=1,
                    channel_id=checkpoint.channel_id,
                    entry_id=checkpoint.entry_id,
                    position_seconds=checkpoint.position_seconds,
                    paused=checkpoint.paused,
                    volume=checkpoint.volume,
                    history_recorded=checkpoint.history_recorded,
                )
            )

    def reserve(self, receipt: Receipt) -> Receipt | None:
        """Return an existing receipt, or commit a reservation before any effect."""
        try:
            with self._session() as session, session.begin():
                session.execute(delete(UndoRow).where(UndoRow.expires_at <= time()))
                row = session.get(RequestRow, receipt.request_id)
                if row is not None:
                    outcome = (
                        (
                            _OUTCOME.validate_python(row.details)
                            if row.details
                            else Outcome(row.code, row.status_code, row.entry_id)
                        )
                        if row.code is not None and row.status_code is not None
                        else None
                    )
                    return Receipt(row.id, row.fingerprint, outcome, row.actor_id)
                session.add(
                    RequestRow(
                        id=receipt.request_id,
                        fingerprint=receipt.fingerprint,
                        actor_id=receipt.actor_id,
                    )
                )
                return None
        except SQLAlchemyError as exc:
            raise StorageError("Cannot reserve control request.") from exc

    @staticmethod
    def _finish(session: Session, receipt: Receipt) -> None:
        row = session.get(RequestRow, receipt.request_id)
        if row is None or receipt.outcome is None:
            raise StorageError("Cannot finish an unreserved control request.")
        if row.code is not None:
            return
        if receipt.consume_undo is not None:
            undo = session.get(UndoRow, receipt.consume_undo)
            if (
                undo is None
                or undo.actor_id != receipt.actor_id
                or undo.expires_at <= time()
            ):
                raise UndoUnavailable()
            session.delete(undo)
        if receipt.removal is not None:
            removal = receipt.removal
            session.add(
                UndoRow(
                    id=removal.id,
                    actor_id=removal.actor_id,
                    expires_at=removal.expires_at.timestamp(),
                    payload=_REMOVAL.dump_python(removal, mode="json"),
                )
            )
        row.code = receipt.outcome.code
        row.status_code = receipt.outcome.status_code
        row.entry_id = receipt.outcome.entry_id
        row.details = _OUTCOME.dump_python(receipt.outcome, mode="json")

    def removal(self, undo_id: UUID, actor_id: UUID | None) -> Removal:
        try:
            with self._session() as session:
                row = session.get(UndoRow, undo_id)
                if row is None or row.actor_id != actor_id or row.expires_at <= time():
                    raise UndoUnavailable()
                return _REMOVAL.validate_python(row.payload)
        except SQLAlchemyError as exc:
            raise StorageError("Cannot read queue undo.") from exc

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
                    update(RequestRow)
                    .where(RequestRow.code.is_(None))
                    .values(code="interrupted", status_code=409)
                )
        except SQLAlchemyError as exc:
            raise StorageError("Cannot recover control requests.") from exc
