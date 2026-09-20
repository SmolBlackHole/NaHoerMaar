# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from uuid import UUID

import pytest

from nahormaar_backend.models import PlaybackState, PlayerSnapshot, QueueEntry
from nahormaar_backend.player import Player
from nahormaar_backend.storage import SQLiteStore, StorageError


def test_metadata_and_order_survive_reopening(tmp_path: Path) -> None:
    path = tmp_path / "player.sqlite3"
    first = QueueEntry(
        "https://youtu.be/first",
        video_id="first",
        title="NaHörMaar: 'een liedje'",
        uploader="An uploader",
        duration_seconds=123.5,
        thumbnail_url="https://example.com/thumbnail.jpg",
    )
    second = QueueEntry("https://youtu.be/second")
    with SQLiteStore(path) as store:
        player = Player(store)
        player.enqueue(first)
        player.enqueue(second)
        player.move_before(second.id, first.id)
    with SQLiteStore(path) as store:
        assert Player(store).snapshot == PlayerSnapshot(upcoming=(second, first))


@pytest.mark.parametrize("state", list(PlaybackState))
def test_recovery_is_persistent_and_does_not_duplicate_entries(
    tmp_path: Path, state: PlaybackState
) -> None:
    path = tmp_path / "player.sqlite3"
    current = (
        None if state is PlaybackState.IDLE else QueueEntry("https://youtu.be/first")
    )
    upcoming = (QueueEntry("https://youtu.be/next"),)
    with SQLiteStore(path) as store:
        store.save(PlayerSnapshot(state, current, upcoming))
    expected = PlayerSnapshot(upcoming=((current,) if current else ()) + upcoming)
    for _ in range(2):
        with SQLiteStore(path) as store:
            assert Player(store).snapshot == expected
            assert store.load() == expected


def test_mid_write_failure_rolls_back_database_and_memory(
    tmp_path: Path, store: SQLiteStore, player: Player
) -> None:
    player.enqueue(QueueEntry("https://youtu.be/first"))
    player.play()
    before = player.snapshot
    path = tmp_path / "player.sqlite3"
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.execute(
            """CREATE TRIGGER reject_entry BEFORE INSERT ON queue_entries
               WHEN NEW.title = 'Rejected'
               BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"""
        )
    with pytest.raises(StorageError, match="injected write failure"):
        player.enqueue(QueueEntry("https://youtu.be/rejected", title="Rejected"))
    assert player.snapshot == before
    assert store.load() == before
    with SQLiteStore(path) as reopened:
        assert reopened.load() == before


def test_commit_failure_does_not_publish_fsm_transition(
    tmp_path: Path, store: SQLiteStore, player: Player
) -> None:
    player.enqueue(QueueEntry("https://youtu.be/first"))
    player.play()
    before = player.snapshot
    path = tmp_path / "player.sqlite3"
    with closing(sqlite3.connect(path, autocommit=True)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM player_state").fetchall()
        with pytest.raises(StorageError, match="locked"):
            player.stop()
        assert player.snapshot == before
        reader.execute("ROLLBACK")
    assert store.load() == before
    player.stop()
    assert store.load() == player.snapshot


def test_failed_recovery_keeps_interrupted_state(tmp_path: Path) -> None:
    path = tmp_path / "player.sqlite3"
    current = QueueEntry("https://youtu.be/first")
    interrupted = PlayerSnapshot(PlaybackState.PLAYING, current)
    with SQLiteStore(path, timeout=0) as store:
        store.save(interrupted)
        with closing(sqlite3.connect(path, autocommit=True)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM player_state").fetchall()
            with pytest.raises(StorageError, match="locked"):
                Player(store)
            reader.execute("ROLLBACK")
        assert store.load() == interrupted
        assert Player(store).snapshot == PlayerSnapshot(upcoming=(current,))


def test_closed_store_does_not_reopen_for_a_player_mutation(tmp_path: Path) -> None:
    path = tmp_path / "closed.sqlite3"
    with SQLiteStore(path) as store:
        player = Player(store)
        player.enqueue(QueueEntry("https://youtu.be/first"))
        player.play()
        before = player.snapshot
    with pytest.raises(StorageError, match="closed"):
        player.stop()
    assert player.snapshot == before
    with SQLiteStore(path) as reopened:
        assert reopened.load() == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE alembic_version SET version_num = 'unknown'",
        "DELETE FROM alembic_version",
        "CREATE TABLE unrelated (id INTEGER)",
        "ALTER TABLE queue_entries ADD COLUMN unexpected TEXT",
        "DELETE FROM player_state",
        "UPDATE player_state SET state = 'unknown'",
        "UPDATE player_state SET state = 'playing'",
        "UPDATE player_state SET current_entry_id = 'not-a-uuid'",
        "UPDATE queue_entries SET position = 9",
        "UPDATE queue_entries SET duration_seconds = -1",
        "UPDATE queue_entries SET duration_seconds = 'invalid'",
        "UPDATE queue_entries SET source_url = ''",
    ],
)
def test_invalid_database_is_rejected_without_replacing_it(
    tmp_path: Path, statement: str
) -> None:
    path = tmp_path / "invalid.sqlite3"
    with SQLiteStore(path) as store:
        Player(store).enqueue(QueueEntry("https://youtu.be/first"))
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.execute(statement)
    contents = path.read_bytes()
    with pytest.raises(StorageError):
        SQLiteStore(path)
    assert path.read_bytes() == contents


def test_foreign_and_corrupted_files_are_not_overwritten(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.sqlite3"
    with closing(sqlite3.connect(foreign, autocommit=True)) as connection:
        connection.execute("CREATE TABLE existing_data (name TEXT)")
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"This is not a SQLite database.")
    for path in (foreign, corrupt):
        contents = path.read_bytes()
        with pytest.raises(StorageError):
            SQLiteStore(path)
        assert path.read_bytes() == contents


def test_committed_queue_survives_process_exit_without_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "crash.sqlite3"
    script = """
import os
import sys
from pathlib import Path
from uuid import UUID
from nahormaar_backend.models import QueueEntry
from nahormaar_backend.player import Player
from nahormaar_backend.storage import SQLiteStore

store = SQLiteStore(Path(sys.argv[1]))
player = Player(store)
player.enqueue(QueueEntry('https://youtu.be/first', id=UUID(int=1)))
player.enqueue(QueueEntry('https://youtu.be/next', id=UUID(int=2)))
player.play()
player.mark_playing()
os._exit(0)
"""
    subprocess.run(  # noqa: S603 - fixed test program and the current Python executable
        [sys.executable, "-c", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    expected = PlayerSnapshot(
        upcoming=(
            QueueEntry("https://youtu.be/first", id=UUID(int=1)),
            QueueEntry("https://youtu.be/next", id=UUID(int=2)),
        )
    )
    with SQLiteStore(path) as store:
        recovered = Player(store).snapshot
        assert recovered.state == expected.state
        assert recovered.upcoming == expected.upcoming
        assert len(recovered.recently_played) == 1
        assert recovered.recently_played[0].entry.id == UUID(int=1)
    with SQLiteStore(path) as store:
        assert Player(store).snapshot == recovered
