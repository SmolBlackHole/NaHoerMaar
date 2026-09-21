# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""One-way import from a read-only legacy snapshot into a new engine database.

This is offline migration code, not a runtime compatibility path. Neither the
source nor an existing destination is ever modified. No provider I/O occurs.
"""

import sqlite3
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from uuid import UUID

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import create_engine, insert, inspect, select, update
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..domain.checkpoint import PlaybackCheckpoint as OldCheckpoint
from ..domain.commands import Outcome as OldOutcome
from ..domain.models import QueueEntry as OldEntry
from ..domain.models import HistoryEntry, PlaybackState, PlayerSnapshot
from ..domain.undo import Removal as OldRemoval
from ..persistence.accounts import account_from_row
from ..persistence.database import Base as OldBase
from ..persistence.models import (
    AccountRow,
    HistoryRow,
    PlaybackCheckpointRow,
    PlayerRow,
    QueueEntryRow,
    RequestRow,
    UndoRow,
)
from .domain.catalog import MediaKind
from .domain.metadata import TrackMetadata
from .domain.queue import (
    Contributor,
    Outcome,
    QueueEntry,
    QueueOrigin,
    Removal,
    RemovedGroup,
)
from .domain.sessions import (
    ListeningSession,
    PlaybackCheckpoint,
    PlaybackIntent,
    PlaybackRecord,
    Receipt,
)
from .domain.tracks import MediaIdentity, Track
from .persistence import (
    Base,
    ListeningSessionRepository,
    OperationRepository,
    PlaybackCheckpointRepository,
    PlaybackRecordRepository,
    QueueRepository,
    TrackRepository,
    database_engine,
    write_transaction,
)
from .schema import upgrade
from .youtube import YouTubeProvider


@dataclass(frozen=True, slots=True)
class MigrationReport:
    session_id: UUID
    tracks: int
    queue_entries: int
    playback_records: int
    receipts: int
    accounts: int
    unidentified_tracks: int


def _entry(row: QueueEntryRow | HistoryRow) -> OldEntry:
    values = {field.name: getattr(row, field.name) for field in fields(OldEntry)}
    if isinstance(row, HistoryRow):
        values["id"] = row.entry_id
    return TypeAdapter(OldEntry).validate_python(values)


async def migrate_copy(
    source: Path, destination: Path, *, now: datetime
) -> MigrationReport:
    """Require the latest old schema and an absent destination. Fail atomically.

    Unknown sources keep their exact reference under the legacy namespace. Old
    history has no end measurements; migration does not invent them. Existing
    receipt IDs remain reserved with the original fingerprint and outcome.
    """
    if now.utcoffset() is None:
        raise ValueError("Migration needs an explicit timezone-aware timestamp.")
    source, destination = source.resolve(strict=True), destination.resolve()
    if source == destination or destination.exists():
        raise ValueError("Migration requires a new, separate destination file.")
    reader = create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(
            source.as_uri() + "?mode=ro", uri=True, autocommit=False
        ),
    )
    try:
        with reader.connect() as connection, DatabaseSession(connection) as database:
            if MigrationContext.configure(connection).get_current_heads() != ("0011",):
                raise ValueError("Expected legacy schema 0011; upgrade a copy first.")
            if (
                compare_metadata(
                    MigrationContext.configure(connection), OldBase.metadata
                )
                or inspect(connection).get_view_names()
            ):
                raise ValueError("Legacy schema differs from revision 0011.")
            if (
                connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
                != "ok"
                or connection.exec_driver_sql("PRAGMA foreign_key_check").first()
            ):
                raise ValueError("Source database failed its integrity checks.")
            player = database.scalars(select(PlayerRow)).one()
            rows = database.scalars(
                select(QueueEntryRow).order_by(QueueEntryRow.position)
            ).all()
            history_rows = database.scalars(
                select(HistoryRow).order_by(HistoryRow.position)
            ).all()
            if [row.position for row in rows] != list(range(len(rows))) or [
                row.position for row in history_rows
            ] != list(range(len(history_rows))):
                raise ValueError("Queue/history positions must be contiguous.")
            entries = tuple(_entry(row) for row in rows)
            current = None
            if player.current_entry_id:
                if not entries or entries[0].id != player.current_entry_id:
                    raise ValueError("Current entry must precede the upcoming queue.")
                current, entries = entries[0], entries[1:]
            history = tuple(
                HistoryEntry(_entry(row), datetime.fromisoformat(row.played_at), row.id)
                for row in history_rows
            )
            PlayerSnapshot(
                PlaybackState(player.state),
                current,
                entries,
                recently_played=history,
                crossfade_seconds=player.crossfade_seconds,
            )
            checkpoint_row = database.scalars(
                select(PlaybackCheckpointRow)
            ).one_or_none()
            checkpoint = (
                OldCheckpoint(
                    **{
                        field.name: getattr(checkpoint_row, field.name)
                        for field in fields(OldCheckpoint)
                    }
                )
                if checkpoint_row
                else None
            )
            if (current and checkpoint is None) or (
                checkpoint and checkpoint.entry_id != (current.id if current else None)
            ):
                raise ValueError("Current entry has no matching recovery checkpoint.")
            if (
                checkpoint
                and checkpoint.history_recorded
                and (not history or history[0].entry.id != checkpoint.entry_id)
            ):
                raise ValueError(
                    "Confirmed current play has no unambiguous latest history record."
                )
            if (
                checkpoint
                and player.state in {"playing", "paused"}
                and checkpoint.paused != (player.state == "paused")
            ):
                raise ValueError("Checkpoint and player disagree about paused intent.")
            requests = database.scalars(select(RequestRow)).all()
            undos = database.scalars(select(UndoRow)).all()
            accounts = database.scalars(select(AccountRow)).all()
            for account in accounts:
                account_from_row(account)
            # Copy unchanged account/session/login columns without exposing tokens.
            account_data = [
                (
                    OldBase.metadata.tables[name],
                    [
                        dict(row)
                        for row in connection.execute(
                            select(OldBase.metadata.tables[name])
                        ).mappings()
                    ],
                )
                for name in ("accounts", "sessions", "login_attempts")
            ]
            database.expunge_all()
    finally:
        reader.dispose()

    settings = ListeningSession(
        channel_id=checkpoint.channel_id if checkpoint else None,
        volume=checkpoint.volume if checkpoint else 1,
        revision=player.revision,
        queue_revision=player.queue_revision,
        crossfade_seconds=player.crossfade_seconds,
    )
    tracks: dict[MediaIdentity, Track] = {}

    def convert(old: OldEntry, position: int = 0) -> QueueEntry:
        reference = YouTubeProvider.identify(old.source_url, kind=MediaKind.TRACK)
        if (
            reference
            and old.video_id
            and old.video_id != reference.identity.external_id
        ):
            raise ValueError(f"Conflicting media identity for entry {old.id}.")
        identity = (
            reference.identity if reference else MediaIdentity("legacy", old.source_url)
        )
        details = TrackMetadata(
            **{field.name: getattr(old, field.name) for field in fields(TrackMetadata)}
        )
        track = tracks.get(identity)
        if track is None:
            track = Track(
                identity,
                reference.source_url if reference else old.source_url,
                details,
                created_at=now,
                updated_at=now,
            )
        else:
            track = replace(track, metadata=track.metadata.merge(details))
        tracks[identity] = track
        return QueueEntry(
            settings.id,
            track.id,
            position,
            Contributor(**asdict(old.added_by)) if old.added_by else None,
            QueueOrigin(old.origin),
            old.id,
        )

    active = convert(current) if current else None
    queued = tuple(convert(entry, position) for position, entry in enumerate(entries))
    records = tuple(
        PlaybackRecord(
            settings.id,
            convert(item.entry).track_id,
            item.entry.id,
            item.played_at,
            Contributor(**asdict(item.entry.added_by)) if item.entry.added_by else None,
            QueueOrigin(item.entry.origin),
            id=item.id,
        )
        for item in history
    )
    recovery = PlaybackCheckpoint(settings.id)
    if active and checkpoint:
        recovery = PlaybackCheckpoint(
            settings.id,
            PlaybackIntent.PAUSED if checkpoint.paused else PlaybackIntent.PLAYING,
            active.id,
            active.track_id,
            history[0].id if checkpoint.history_recorded else None,
            checkpoint.position_seconds,
            active.added_by,
            active.origin,
        )
    removals: dict[UUID, Removal] = {}
    for row in undos:
        old = TypeAdapter(OldRemoval).validate_python(row.payload)
        if (
            old.id != row.id
            or old.actor_id != row.actor_id
            or old.expires_at.timestamp() != row.expires_at
        ):
            raise ValueError(f"Inconsistent removal {row.id}.")
        removals[row.id] = Removal(
            old.id,
            old.actor_id,
            old.expires_at,
            tuple(
                RemovedGroup(
                    tuple(
                        convert(entry, index)
                        for index, entry in enumerate(group.entries)
                    ),
                    group.previous_id,
                    group.next_id,
                )
                for group in old.groups
            ),
        )
    receipts: list[Receipt] = []
    for request in requests:
        old_outcome = (
            TypeAdapter(OldOutcome).validate_python(request.details)
            if request.details
            else OldOutcome(
                code=request.code or "interrupted",
                status_code=request.status_code or 409,
                entry_id=request.entry_id,
            )
        )
        if request.code is not None and old_outcome.code != request.code:
            raise ValueError(f"Inconsistent receipt {request.id}.")
        actor = Contributor(**asdict(old_outcome.actor)) if old_outcome.actor else None
        outcome = Outcome(
            old_outcome.code,
            old_outcome.added_count,
            old_outcome.removed_count,
            old_outcome.restored_count,
            old_outcome.skipped_count,
            tuple(
                convert(entry, index) for index, entry in enumerate(old_outcome.entries)
            ),
            actor,
            old_outcome.undo_id,
            old_outcome.undo_expires_at,
        )
        receipts.append(
            Receipt(
                request.id, settings.id, request.fingerprint, request.actor_id, outcome
            )
        )

    # Exclusive creation makes retries and an existing destination safe.
    with destination.open("xb"):
        pass
    engine = database_engine(destination)
    complete = False
    try:
        async with engine.begin() as target_connection:
            await target_connection.run_sync(upgrade)
        sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
        async with write_transaction(sessions) as target_database:
            await ListeningSessionRepository(target_database).add(settings)
            for track in tracks.values():
                await TrackRepository(target_database).add(track)
            await QueueRepository(target_database).replace(settings.id, queued)
            for record in records:
                await PlaybackRecordRepository(target_database).add(record)
            await PlaybackCheckpointRepository(target_database).save(recovery)
            operations = OperationRepository(target_database)
            for receipt in receipts:
                await operations.add(
                    receipt,
                    removal=removals.pop(receipt.outcome.undo_id, None)
                    if receipt.outcome.undo_id
                    else None,
                )
            evidence = Base.metadata.tables["operation_receipts"]
            for request in requests:
                await target_database.execute(
                    update(evidence)
                    .where(evidence.c.request_id == request.id)
                    .values(
                        imported_outcome={
                            "code": request.code,
                            "status_code": request.status_code,
                            "entry_id": str(request.entry_id)
                            if request.entry_id
                            else None,
                            "details": request.details,
                        }
                    )
                )
            if removals:
                raise ValueError("A removal has no originating operation receipt.")
            for table, values in account_data:
                if values:
                    await target_database.execute(insert(table), values)
        async with engine.connect() as target_connection:
            if (
                await target_connection.exec_driver_sql("PRAGMA foreign_key_check")
            ).first():
                raise ValueError("Migrated database contains a broken reference.")
        complete = True
    finally:
        await engine.dispose()
        if not complete:
            destination.unlink()
    return MigrationReport(
        settings.id,
        len(tracks),
        len(queued),
        len(records),
        len(receipts),
        len(accounts),
        sum(track.identity.namespace == "legacy" for track in tracks.values()),
    )
