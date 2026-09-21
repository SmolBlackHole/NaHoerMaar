# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session as DatabaseSession

from nahormaar_backend.domain.commands import Outcome as OldOutcome
from nahormaar_backend.domain.models import Contributor, QueueEntry
from nahormaar_backend.domain.undo import Removal, RemovedGroup
from nahormaar_backend.persistence.database import database_engine as old_engine
from nahormaar_backend.persistence.migrations import upgrade as old_upgrade
from nahormaar_backend.persistence.models import (
    AccountRow,
    HistoryRow,
    LoginRow,
    PlayerRow,
    PlaybackCheckpointRow,
    QueueEntryRow,
    RequestRow,
    SessionRow,
    UndoRow,
)
from nahormaar_backend.engine.domain.queue import (
    Add,
    Contributor as EngineContributor,
    Undo,
)
from nahormaar_backend.engine.domain.sessions import PlaybackIntent
from nahormaar_backend.engine.migration import migrate_copy
from nahormaar_backend.engine.persistence import (
    OperationRepository,
    TrackRepository,
    database_engine,
)
from nahormaar_backend.engine.schema import metadata, REVISION
from nahormaar_backend.engine.session import Session
from .test_catalog import TIME

ACTOR = Contributor(UUID(int=1), "Listener", "0001")
CURRENT, NEXT, REMOVED, PLAY, REQUEST, UNDO = (UUID(int=i) for i in range(2, 8))


def legacy(path: Path, *, paused: bool = False, confirmed: bool = True) -> None:
    engine = old_engine(path)
    current = QueueEntry(
        "https://music.youtube.com/watch?v=abcdefghijk",
        CURRENT,
        title="Амура",
        artist="Artist, with comma",
        added_by=ACTOR,
    )
    duplicate = QueueEntry(
        "https://youtu.be/abcdefghijk", NEXT, title="Амура", added_by=ACTOR
    )
    removed = QueueEntry(
        "https://youtu.be/ZYXWVUTSRQP", REMOVED, title="Removed", added_by=ACTOR
    )
    undo = Removal(
        UNDO,
        ACTOR.id,
        TIME + timedelta(seconds=12),
        (RemovedGroup((removed,), NEXT, None),),
    )
    outcome = OldOutcome(
        removed_count=1,
        entries=(removed,),
        actor=ACTOR,
        undo_id=UNDO,
        undo_expires_at=undo.expires_at,
    )
    try:
        with engine.begin() as connection:
            old_upgrade(connection)
        with DatabaseSession(engine) as database, database.begin():
            database.add(
                AccountRow(
                    id=ACTOR.id,
                    discord_id="243718053362270208",
                    name=ACTOR.name,
                    avatar=ACTOR.avatar,
                    profile_complete=True,
                    appearance={"primaryColor": "teal"},
                )
            )
            database.flush()
            database.add_all(
                [
                    SessionRow(
                        token_hash="fixture-token-hash",  # noqa: S106 - synthetic hash
                        account_id=ACTOR.id,
                        expires_at=123456,
                    ),
                    LoginRow(
                        state_hash="fixture-state",
                        browser_hash="fixture-browser",
                        verifier="fixture-verifier",
                        expires_at=123456,
                    ),
                    QueueEntryRow(
                        **(
                            asdict(current)
                            | {
                                "added_by": TypeAdapter(Contributor).dump_python(
                                    ACTOR, mode="json"
                                )
                            }
                        ),
                        position=0,
                    ),
                    QueueEntryRow(
                        **(
                            asdict(duplicate)
                            | {
                                "added_by": TypeAdapter(Contributor).dump_python(
                                    ACTOR, mode="json"
                                )
                            }
                        ),
                        position=1,
                    ),
                ]
            )
            database.flush()
            database.merge(
                PlayerRow(
                    id=1,
                    state="paused" if paused else "playing",
                    current_entry_id=CURRENT,
                    revision=8,
                    queue_revision=4,
                    crossfade_seconds=5,
                )
            )
            database.add(
                PlaybackCheckpointRow(
                    id=1,
                    channel_id=123,
                    entry_id=CURRENT,
                    position_seconds=12.5,
                    paused=paused,
                    volume=0.4,
                    history_recorded=confirmed,
                )
            )
            history_values = asdict(current)
            history_values["added_by"] = TypeAdapter(Contributor).dump_python(
                ACTOR, mode="json"
            )
            history_values.pop("id")
            database.add(
                HistoryRow(
                    id=PLAY,
                    entry_id=CURRENT,
                    position=0,
                    played_at=TIME.isoformat(),
                    **history_values,
                )
            )
            database.add(
                RequestRow(
                    id=REQUEST,
                    fingerprint="legacy-removal",
                    code="ok",
                    status_code=200,
                    entry_id=None,
                    actor_id=ACTOR.id,
                    details=TypeAdapter(OldOutcome).dump_python(outcome, mode="json"),
                )
            )
            database.add(
                UndoRow(
                    id=UNDO,
                    actor_id=ACTOR.id,
                    expires_at=undo.expires_at.timestamp(),
                    payload=TypeAdapter(Removal).dump_python(undo, mode="json"),
                )
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
def test_migrate_identity_accounts_receipts_undo_and_recovery(
    tmp_path: Path, paused: bool, confirmed: bool
) -> None:
    async def scenario() -> None:
        source, target = tmp_path / "legacy.db", tmp_path / "engine.db"
        legacy(source, paused=paused, confirmed=confirmed)
        original = source.read_bytes()
        report = await migrate_copy(source, target, now=TIME)
        assert source.read_bytes() == original
        assert (
            report.tracks,
            report.queue_entries,
            report.playback_records,
            report.receipts,
            report.accounts,
        ) == (2, 1, 1, 1, 1)
        engine = database_engine(target)
        sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                    == REVISION
                )
                assert (
                    await connection.run_sync(
                        lambda conn: compare_metadata(
                            MigrationContext.configure(conn), metadata()
                        )
                    )
                    == []
                )
                assert (
                    await connection.execute(select(AccountRow.appearance))
                ).scalar_one() == {"primaryColor": "teal"}
                assert (
                    await connection.scalar(select(SessionRow.token_hash))
                    == "fixture-token-hash"
                )
                assert (
                    await connection.scalar(select(LoginRow.verifier))
                    == "fixture-verifier"
                )
                assert "Removed" in str(
                    await connection.scalar(
                        text("SELECT imported_outcome FROM operation_receipts")
                    )
                )
            owner = await Session.open(sessions, report.session_id, clock=lambda: TIME)
            try:
                state = owner.snapshot
                assert (
                    state.settings.revision == 8 and state.settings.queue_revision == 4
                )
                assert (
                    state.settings.volume == 0.4
                    and state.settings.crossfade_seconds == 5
                )
                assert state.checkpoint.position_seconds == 12.5
                assert state.checkpoint.intent == (
                    PlaybackIntent.PAUSED if paused else PlaybackIntent.PLAYING
                )
                assert state.checkpoint.play_id == (PLAY if confirmed else None)
                assert state.history[0].id == PLAY and state.history[0].ended_at is None
                assert state.queue.entries[0].id == NEXT
                assert state.queue.entries[0].track_id == state.checkpoint.track_id
                async with sessions.begin() as database:
                    track = await TrackRepository(database).get(
                        state.queue.entries[0].track_id
                    )
                    assert track and track.metadata.title == "Амура"
                    assert (
                        track.metadata.artist == "Artist, with comma"
                        and track.artist_ids == ()
                    )
                    receipt = await OperationRepository(database).get(REQUEST)
                    assert receipt and receipt.fingerprint == "legacy-removal"
                    assert receipt.outcome.entries[0].id == REMOVED
                actor = EngineContributor(**asdict(ACTOR))
                conflict = await owner.request(
                    REQUEST, Add((state.queue.entries[0].track_id,)), actor=actor
                )
                assert conflict.outcome.code == "idempotency_conflict"
                undone = await owner.request(UUID(int=90), Undo(UNDO), actor=actor)
                assert undone.outcome.restored_count == 1
                assert [entry.id for entry in undone.snapshot.queue.entries] == [
                    NEXT,
                    REMOVED,
                ]
            finally:
                await owner.close()
        finally:
            await engine.dispose()
        before_target = target.read_bytes()
        with pytest.raises(ValueError, match="separate destination"):
            await migrate_copy(source, target, now=TIME)
        assert target.read_bytes() == before_target

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE alembic_version",
        "INSERT INTO alembic_version (version_num) VALUES ('unexpected')",
        "ALTER TABLE queue_entries ADD COLUMN surprise TEXT",
        "UPDATE queue_entries SET position=9 WHERE position=1",
        "UPDATE playback_checkpoint SET entry_id=NULL",
        "UPDATE playback_history SET entry_id='00000000000000000000000000000063'",
        "UPDATE accounts SET avatar='invalid'",
        "UPDATE queue_entries SET video_id='ZZZZZZZZZZZ'",
        "UPDATE queue_entries SET duration_seconds=-1",
        "UPDATE requests SET code='different'",
        "DELETE FROM requests",
    ],
)
def test_rejected_migration_keeps_source_and_has_no_partial_target(
    tmp_path: Path, statement: str
) -> None:
    source, target = tmp_path / "legacy.db", tmp_path / "engine.db"
    legacy(source)
    engine = old_engine(source)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()
    original = source.read_bytes()
    with pytest.raises(ValueError):
        asyncio.run(migrate_copy(source, target, now=TIME))
    assert source.read_bytes() == original and not target.exists()


def test_unknown_reference_is_retained_without_title_matching(tmp_path: Path) -> None:
    source, target = tmp_path / "legacy.db", tmp_path / "engine.db"
    legacy(source)
    engine = old_engine(source)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE queue_entries SET source_url='unknown-source' WHERE position=1"
                )
            )
    finally:
        engine.dispose()
    report = asyncio.run(migrate_copy(source, target, now=TIME))
    assert report.tracks == 3 and report.unidentified_tracks == 1
