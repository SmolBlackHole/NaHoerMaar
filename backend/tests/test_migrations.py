# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.commands import Outcome, Receipt
from nahormaar_backend.models import QueueEntry
from nahormaar_backend.player import Player
from nahormaar_backend.storage import SQLiteStore, StorageError


def version_one(path: Path) -> None:
    with closing(sqlite3.connect(path, autocommit=True)) as db:
        db.executescript("""
            CREATE TABLE queue_entries (
                id CHAR(32) PRIMARY KEY, position INTEGER NOT NULL UNIQUE,
                source_url TEXT NOT NULL, video_id TEXT, title TEXT, uploader TEXT,
                duration_seconds FLOAT, thumbnail_url TEXT
            );
            CREATE TABLE player_state (
                id INTEGER PRIMARY KEY, state TEXT NOT NULL,
                current_entry_id CHAR(32) REFERENCES queue_entries(id)
            );
            PRAGMA user_version = 1;
        """)
        for i in (1, 2):
            db.execute(
                "INSERT INTO queue_entries (id, position, source_url) VALUES (?, ?, ?)",
                (UUID(int=i).hex, i - 1, f"https://youtu.be/{i}"),
            )
        db.execute(
            "INSERT INTO player_state VALUES (1, 'playing', ?)", (UUID(int=1).hex,)
        )


def test_migrate_v1_recovers_current_preserving_ids_order_and_revisions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.sqlite3"
    version_one(path)
    with SQLiteStore(path) as store:
        player = Player(store)
        assert [entry.id for entry in player.snapshot.upcoming] == [
            UUID(int=1),
            UUID(int=2),
        ]
        assert player.snapshot.current is None
        assert player.revisions.revision == player.revisions.queue_revision == 1
    with SQLiteStore(path) as store:
        assert Player(store).revisions.revision == 1
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE player_state SET state = 'invalid'",
        "UPDATE queue_entries SET duration_seconds = 'bad'",
        "ALTER TABLE queue_entries ADD COLUMN unexpected TEXT",
    ],
)
def test_failed_migration_leaves_old_database_unchanged(
    tmp_path: Path, statement: str
) -> None:
    path = tmp_path / "invalid.sqlite3"
    version_one(path)
    with closing(sqlite3.connect(path, autocommit=True)) as db:
        db.execute(statement)
    before = path.read_bytes()
    with pytest.raises(StorageError):
        SQLiteStore(path)
    assert path.read_bytes() == before


def test_outcome_failure_rolls_back_queue_and_revision(tmp_path: Path) -> None:
    path = tmp_path / "player.sqlite3"
    with SQLiteStore(path) as store:
        player = Player(store)
        before = player.revisions
        receipt = Receipt(uuid4(), "add", Outcome())
        player.reserve(receipt)
        with closing(sqlite3.connect(path, autocommit=True)) as db:
            db.execute("""CREATE TRIGGER reject_outcome BEFORE UPDATE ON requests
                          BEGIN SELECT RAISE(ABORT, 'receipt failed'); END""")
        with pytest.raises(StorageError):
            player.apply_request(
                receipt, lambda p: p.enqueue(QueueEntry("https://youtu.be/first"))
            )
        assert player.snapshot.upcoming == store.load().upcoming == ()
        assert player.revisions == store.revisions() == before
        assert store.reserve(receipt) == Receipt(receipt.request_id, "add")


@pytest.mark.parametrize("corrupt", [False, True])
def test_migrate_v2_preserves_receipts_and_rolls_back_invalid_data(
    tmp_path: Path, corrupt: bool
) -> None:
    path = tmp_path / "v2.sqlite3"
    version_one(path)
    request_id = uuid4()
    with closing(sqlite3.connect(path, autocommit=True)) as db:
        db.executescript("""
            ALTER TABLE player_state ADD COLUMN revision INTEGER NOT NULL DEFAULT 9;
            ALTER TABLE player_state ADD COLUMN queue_revision INTEGER NOT NULL DEFAULT 4;
            CREATE TABLE requests (
                id CHAR(32) PRIMARY KEY, fingerprint TEXT NOT NULL, code TEXT,
                status_code INTEGER, entry_id CHAR(32)
            );
            PRAGMA user_version = 2;
        """)
        db.execute(
            "INSERT INTO requests VALUES (?, 'add', 'ok', 200, NULL)", (request_id.hex,)
        )
        if corrupt:
            db.execute("UPDATE player_state SET state = 'invalid'")
    before = path.read_bytes()
    if corrupt:
        with pytest.raises(StorageError):
            SQLiteStore(path)
        assert path.read_bytes() == before
    else:
        with SQLiteStore(path) as store:
            assert store.revisions().revision == 9
            assert store.revisions().queue_revision == 4
            assert store.load().recently_played == ()
            assert store.reserve(Receipt(request_id, "add")) == Receipt(
                request_id, "add", Outcome()
            )


@pytest.mark.parametrize("corrupt", [False, True])
def test_migrate_v3_preserves_queue_and_history_or_rolls_back(
    tmp_path: Path, corrupt: bool
) -> None:
    path = tmp_path / "v3.sqlite3"
    version_one(path)
    with closing(sqlite3.connect(path, autocommit=True)) as db:
        db.executescript("""
            ALTER TABLE player_state ADD COLUMN revision INTEGER NOT NULL DEFAULT 9;
            ALTER TABLE player_state ADD COLUMN queue_revision INTEGER NOT NULL DEFAULT 4;
            ALTER TABLE queue_entries ADD COLUMN artist VARCHAR;
            ALTER TABLE queue_entries ADD COLUMN uploader_url VARCHAR;
            CREATE TABLE requests (
                id CHAR(32) PRIMARY KEY, fingerprint TEXT NOT NULL, code TEXT,
                status_code INTEGER, entry_id CHAR(32)
            );
            CREATE TABLE playback_history (
                id CHAR(32) PRIMARY KEY, position INTEGER NOT NULL UNIQUE,
                played_at VARCHAR NOT NULL, entry_id CHAR(32) NOT NULL,
                source_url VARCHAR NOT NULL, video_id VARCHAR, title VARCHAR,
                uploader VARCHAR, duration_seconds FLOAT, thumbnail_url VARCHAR,
                artist VARCHAR, uploader_url VARCHAR
            );
            PRAGMA user_version = 3;
        """)
        db.execute(
            "INSERT INTO playback_history (id, position, played_at, entry_id, source_url, title, artist) "
            "VALUES (?, 0, ?, ?, 'https://youtu.be/1', 'Song', 'Artist')",
            (
                UUID(int=3).hex,
                "invalid" if corrupt else "2026-09-19T12:00:00+00:00",
                UUID(int=1).hex,
            ),
        )
    before = path.read_bytes()
    if corrupt:
        with pytest.raises(StorageError):
            SQLiteStore(path)
        assert path.read_bytes() == before
    else:
        with SQLiteStore(path) as store:
            snapshot = store.load()
            assert snapshot.current is not None and snapshot.current.added_by is None
            assert snapshot.upcoming[0].added_by is None
            assert snapshot.recently_played[0].entry.title == "Song"
            assert snapshot.recently_played[0].entry.artist == "Artist"
            assert snapshot.recently_played[0].entry.added_by is None
            assert store.revisions().revision == 9
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 4


@pytest.mark.parametrize("commit", [False, True])
def test_process_crash_preserves_reservation_or_atomic_outcome(
    tmp_path: Path, commit: bool
) -> None:
    path = tmp_path / "crash.sqlite3"
    script = """
import os, sys
from pathlib import Path
from uuid import UUID
from nahormaar_backend.commands import Receipt, Outcome
from nahormaar_backend.models import QueueEntry
from nahormaar_backend.player import Player
from nahormaar_backend.storage import SQLiteStore
store = SQLiteStore(Path(sys.argv[1]))
player = Player(store)
receipt = Receipt(UUID(int=1), 'add', Outcome(entry_id=UUID(int=2)))
player.reserve(receipt)
if sys.argv[2] == 'True':
    player.apply_request(receipt, lambda p: p.enqueue(QueueEntry('https://youtu.be/first', id=UUID(int=2))))
os._exit(0)
"""
    subprocess.run(  # noqa: S603 - fixed crash fixture with the current Python
        [sys.executable, "-c", script, str(path), str(commit)],
        check=True,
        capture_output=True,
        timeout=15,
    )
    with SQLiteStore(path) as store:
        player = Player(store)
        player.recover_requests()
        receipt = player.reserve(Receipt(UUID(int=1), "add"))
        assert receipt is not None
        assert receipt.outcome == (
            Outcome(entry_id=UUID(int=2)) if commit else Outcome("interrupted", 409)
        )
        assert len(player.snapshot.upcoming) == int(commit)
