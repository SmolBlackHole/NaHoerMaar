# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from nahormaar_backend.domain.identity import Contributor
from nahormaar_backend.engine.domain.catalog import MediaKind, MediaReference
from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.queue import QueueEntry, QueueOrigin
from nahormaar_backend.engine.domain.radio import RadioStrategy
from nahormaar_backend.engine.domain.sessions import (
    PlaybackCheckpoint,
    PlaybackIntent,
    PlaybackRecord,
)
from nahormaar_backend.engine.domain.tracks import MediaIdentity, Track
from nahormaar_backend.engine.persistence import (
    ListeningSessionRepository,
    PlaybackCheckpointRepository,
    PlaybackRecordRepository,
    QueueRepository,
    RadioStrategyRepository,
    TrackRepository,
    database_engine,
    write_transaction,
)
from nahormaar_backend.engine.schema import initialize
from nahormaar_backend.persistence.accounts import Accounts
from nahormaar_backend.recovery import (
    RecoveryError,
    backup_database,
    main,
    restore_database,
    verify_database,
)

TIME = datetime(2026, 9, 23, 10, 30, tzinfo=UTC)


async def _populate(
    path: Path,
) -> tuple[
    UUID,
    Track,
    QueueEntry,
    PlaybackRecord,
    PlaybackCheckpoint,
    RadioStrategy,
]:
    session_id = await initialize(path)
    actor = Contributor(uuid4(), "Spoon", "abcd")
    track = Track(
        MediaIdentity("youtube", "recovery-test"),
        "https://music.youtube.com/watch?v=recovery-test",
        TrackMetadata(
            title="Recovery song", artist="Test artist", duration_seconds=180
        ),
        created_at=TIME,
        updated_at=TIME,
    )
    upcoming = QueueEntry(session_id, track.id, 0, actor, QueueOrigin.RADIO)
    current_entry = uuid4()
    record = PlaybackRecord(
        session_id,
        track.id,
        current_entry,
        TIME,
        actor,
        QueueOrigin.RADIO,
    )
    checkpoint = PlaybackCheckpoint(
        session_id,
        PlaybackIntent.PAUSED,
        current_entry,
        track.id,
        record.id,
        73.5,
        actor,
        QueueOrigin.RADIO,
    )
    strategy = RadioStrategy(
        MediaReference(track.identity, MediaKind.TRACK, track.source_url), actor
    )
    engine = database_engine(path)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
        async with write_transaction(sessions) as database:
            assert await ListeningSessionRepository(database).get(session_id)
            await TrackRepository(database).add(track)
            await QueueRepository(database).add(upcoming)
            await PlaybackRecordRepository(database).add(record)
            await PlaybackCheckpointRepository(database).save(checkpoint)
            await RadioStrategyRepository(database).save(session_id, strategy)
    finally:
        await engine.dispose()
    accounts = Accounts(path)
    try:
        accounts.create_session(
            "243718053362270208",
            "Spoon",
            "abcd",
            "recovery-token",
            TIME.timestamp() + 3600,
            TIME.timestamp(),
            None,
        )
    finally:
        accounts.close()
    return session_id, track, upcoming, record, checkpoint, strategy


async def _assert_restored(
    path: Path,
    expected: tuple[
        UUID,
        Track,
        QueueEntry,
        PlaybackRecord,
        PlaybackCheckpoint,
        RadioStrategy,
    ],
) -> None:
    session_id, track, upcoming, record, checkpoint, strategy = expected
    assert await initialize(path) == session_id
    engine = database_engine(path)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
        async with sessions.begin() as database:
            assert await TrackRepository(database).get(track.id) == track
            assert await QueueRepository(database).entries(session_id) == (upcoming,)
            assert await PlaybackRecordRepository(database).recent(session_id) == (
                record,
            )
            assert await PlaybackCheckpointRepository(database).get(session_id) == (
                checkpoint
            )
            assert await RadioStrategyRepository(database).get(session_id) == strategy
    finally:
        await engine.dispose()
    accounts = Accounts(path)
    try:
        account = accounts.session("recovery-token", TIME.timestamp())
        assert account and account[0].profile.name == "Spoon"
    finally:
        accounts.close()


def test_online_backup_and_fresh_restore_preserve_shared_state(tmp_path: Path) -> None:
    source = tmp_path / "engine.sqlite3"
    expected = asyncio.run(_populate(source))
    directory = tmp_path / "backups"

    with sqlite3.connect(source) as active_database:
        assert active_database.execute("SELECT COUNT(*) FROM queue_entries").fetchone()
        backup = backup_database(source, directory, now=TIME)

    assert backup.name == "engine-20260923T103000000000Z.sqlite3"
    verify_database(backup)
    restored = restore_database(backup, tmp_path / "restored.sqlite3")
    asyncio.run(_assert_restored(restored, expected))

    replacement = tmp_path / "replacement.sqlite3"
    asyncio.run(initialize(replacement))
    Path(f"{replacement}-wal").touch()
    Path(f"{replacement}-shm").touch()
    restore_database(backup, replacement, replace=True)
    assert not Path(f"{replacement}-wal").exists()
    assert not Path(f"{replacement}-shm").exists()
    asyncio.run(_assert_restored(replacement, expected))


def test_retention_only_removes_managed_backups(tmp_path: Path) -> None:
    source = tmp_path / "engine.sqlite3"
    asyncio.run(_populate(source))
    directory = tmp_path / "backups"
    directory.mkdir()
    unrelated = directory / "notes.sqlite3"
    unrelated.write_text("keep me", encoding="utf-8")

    created = [
        backup_database(source, directory, now=TIME + timedelta(microseconds=index))
        for index in range(16)
    ]

    remaining = sorted(directory.glob("engine-*.sqlite3"))
    assert remaining == created[2:]
    assert unrelated.read_text(encoding="utf-8") == "keep me"


def test_invalid_restore_leaves_destination_unchanged(tmp_path: Path) -> None:
    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_bytes(b"not a sqlite database")
    destination = tmp_path / "engine.sqlite3"
    destination.write_bytes(b"existing database")

    with pytest.raises(RecoveryError):
        restore_database(damaged, destination, replace=True)

    assert destination.read_bytes() == b"existing database"


def test_restore_requires_explicit_replacement(tmp_path: Path) -> None:
    source = tmp_path / "engine.sqlite3"
    asyncio.run(_populate(source))
    backup = backup_database(source, tmp_path / "backups", now=TIME)
    before = source.read_bytes()

    with pytest.raises(RecoveryError, match="--replace"):
        restore_database(backup, source)

    assert source.read_bytes() == before


def test_verify_rejects_foreign_schema(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(foreign) as database:
        database.execute("CREATE TABLE something_else (value TEXT)")

    with pytest.raises(RecoveryError):
        verify_database(foreign)


def test_cli_uses_database_path_and_reports_scheduler_friendly_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "engine.sqlite3"
    asyncio.run(_populate(source))
    directory = tmp_path / "backups"

    assert (
        main(
            ("backup", "--directory", str(directory)),
            {"DATABASE_PATH": str(source)},
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.out.startswith("Backup created: ") and not output.err

    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_text("broken", encoding="utf-8")
    assert main(("verify", str(damaged)), {}) == 1
    output = capsys.readouterr()
    assert not output.out and output.err.startswith("Recovery failed: ")
