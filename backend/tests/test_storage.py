# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.application.player import Player
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.commands import Outcome, Receipt, Revisions
from nahormaar_backend.domain.models import (
    HistoryEntry,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
)
from nahormaar_backend.domain.undo import UndoUnavailable
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError


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
    player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.LOADING, QueueEntry("https://youtu.be/first")),
        None,
    )
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
    entry = QueueEntry("https://youtu.be/first")
    player.commit_lifecycle(PlayerSnapshot(PlaybackState.LOADING, entry), None)
    before = player.snapshot
    stopped = PlayerSnapshot(upcoming=(entry,))
    path = tmp_path / "player.sqlite3"
    with closing(sqlite3.connect(path, autocommit=True)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM player_state").fetchall()
        with pytest.raises(StorageError, match="locked"):
            player.commit_lifecycle(stopped, None)
        assert player.snapshot == before
        reader.execute("ROLLBACK")
    assert store.load() == before
    player.commit_lifecycle(stopped, None)
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
        entry = QueueEntry("https://youtu.be/first")
        player.commit_lifecycle(PlayerSnapshot(PlaybackState.LOADING, entry), None)
        before = player.snapshot
    with pytest.raises(StorageError, match="closed"):
        player.commit_lifecycle(PlayerSnapshot(upcoming=(entry,)), None)
    assert player.snapshot == before
    with SQLiteStore(path) as reopened:
        assert reopened.load() == before


def test_snapshot_checkpoint_and_receipt_roll_back_together(
    tmp_path: Path, store: SQLiteStore, player: Player
) -> None:
    first, second = QueueEntry("first"), QueueEntry("second")
    checkpoint = PlaybackCheckpoint(7, first.id, 35, volume=0.4, history_recorded=True)
    player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.PLAYING, first, (second,)),
        checkpoint,
        record_history=True,
    )
    before, revisions = player.snapshot, player.revisions
    receipt = Receipt(uuid4(), "skip")
    store.reserve(receipt)
    with closing(sqlite3.connect(tmp_path / "player.sqlite3", autocommit=True)) as db:
        db.execute(
            """CREATE TRIGGER reject_checkpoint BEFORE INSERT ON playback_checkpoint
               WHEN NEW.position_seconds = 0
               BEGIN SELECT RAISE(ABORT, 'injected checkpoint failure'); END"""
        )
    with pytest.raises(StorageError, match="injected checkpoint failure"):
        player.apply_request(
            replace(receipt, outcome=Outcome()),
            lambda p: p.commit_lifecycle(
                replace(
                    before, state=PlaybackState.LOADING, current=second, upcoming=()
                ),
                PlaybackCheckpoint(7, second.id, volume=0.4),
            ),
        )
    assert player.snapshot == store.load() == before
    assert player.revisions == store.revisions() == revisions
    assert store.checkpoint() == checkpoint
    assert store.reserve(receipt) == receipt


def test_store_writes_explicit_checkpoint_without_applying_playback_policy(
    store: SQLiteStore,
) -> None:
    entry = QueueEntry("track")
    snapshot = PlayerSnapshot(PlaybackState.PLAYING, entry)
    checkpoint = PlaybackCheckpoint(7, entry.id, 35, True, 0.4)
    revisions = Revisions(4, 2)
    store.save(snapshot, checkpoint=checkpoint, revisions=revisions)
    assert store.load() == snapshot
    assert store.checkpoint() == checkpoint
    assert store.revisions() == revisions


def test_unavailable_undo_keeps_domain_error_and_rolls_back_checkpoint(
    store: SQLiteStore, player: Player
) -> None:
    current = QueueEntry("current")
    checkpoint = PlaybackCheckpoint(7, current.id, 35)
    player.commit_lifecycle(PlayerSnapshot(PlaybackState.LOADING, current), checkpoint)
    before, revisions = player.snapshot, player.revisions
    reservation = Receipt(uuid4(), "undo", actor_id=uuid4())
    store.reserve(reservation)
    receipt = replace(reservation, outcome=Outcome(), consume_undo=uuid4())
    with pytest.raises(UndoUnavailable):
        player.apply_request(
            receipt,
            lambda p: p.commit_lifecycle(
                PlayerSnapshot(upcoming=(current,)), PlaybackCheckpoint(7, None)
            ),
        )
    assert player.snapshot == store.load() == before
    assert player.revisions == store.revisions() == revisions
    assert store.checkpoint() == checkpoint
    assert store.reserve(reservation) == reservation


def test_standalone_store_save_retains_existing_checkpoint_reconciliation(
    store: SQLiteStore,
) -> None:
    first, second = QueueEntry("first"), QueueEntry("second")
    store.save(PlayerSnapshot(PlaybackState.PLAYING, first, (second,)))
    store.save_checkpoint(
        PlaybackCheckpoint(7, first.id, 35, volume=0.4, history_recorded=True)
    )
    store.save(PlayerSnapshot(PlaybackState.PAUSED, first, (second,)))
    assert store.checkpoint() == PlaybackCheckpoint(
        7, first.id, 35, True, 0.4, history_recorded=True
    )
    store.save(PlayerSnapshot(PlaybackState.LOADING, second))
    assert store.checkpoint() == PlaybackCheckpoint(7, second.id, volume=0.4)
    store.save(PlayerSnapshot(PlaybackState.ERROR, second))
    assert store.checkpoint() is None


@pytest.mark.parametrize("recorded", [False, True])
def test_checkpoint_confirmation_is_explicit_and_survives_reopening(
    tmp_path: Path, recorded: bool
) -> None:
    entry = QueueEntry("track")
    snapshot = PlayerSnapshot(
        PlaybackState.LOADING,
        entry,
        recently_played=(HistoryEntry(entry, datetime.now(UTC)),),
    )
    checkpoint = PlaybackCheckpoint(7, entry.id, history_recorded=recorded)
    path = tmp_path / "confirmed.sqlite3"
    with SQLiteStore(path) as store:
        store.save(snapshot, checkpoint=checkpoint)
    with SQLiteStore(path) as store:
        assert store.load() == snapshot
        assert store.checkpoint() == checkpoint


def test_idle_checkpoint_cannot_claim_a_confirmed_play() -> None:
    with pytest.raises(ValueError, match="idle checkpoint"):
        PlaybackCheckpoint(7, None, history_recorded=True)


def test_confirmed_lifecycle_commits_confirmation_with_history(
    player: Player, store: SQLiteStore
) -> None:
    entry = QueueEntry("track")
    player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.LOADING, entry), PlaybackCheckpoint(7, entry.id)
    )

    player.commit_lifecycle(
        replace(player.snapshot, state=PlaybackState.PLAYING),
        PlaybackCheckpoint(7, entry.id, history_recorded=True),
        record_history=True,
    )

    assert store.checkpoint() == PlaybackCheckpoint(7, entry.id, history_recorded=True)
    assert len(store.load().recently_played) == 1


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
from nahormaar_backend.domain.models import PlaybackState, PlayerSnapshot, QueueEntry
from nahormaar_backend.application.player import Player
from nahormaar_backend.persistence.player_store import SQLiteStore

store = SQLiteStore(Path(sys.argv[1]))
player = Player(store)
first = QueueEntry('https://youtu.be/first', id=UUID(int=1))
second = QueueEntry('https://youtu.be/next', id=UUID(int=2))
player.commit_lifecycle(
    PlayerSnapshot(PlaybackState.PLAYING, first, (second,)),
    None,
    record_history=True,
)
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
