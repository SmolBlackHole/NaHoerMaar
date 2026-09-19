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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


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
