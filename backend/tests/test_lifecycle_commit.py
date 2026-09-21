# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from nahormaar_backend.application.player import Player
from nahormaar_backend.application.storage import StorageError
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.commands import Outcome, Receipt, Revisions
from nahormaar_backend.domain.fsm import (
    LifecycleEvent,
    PlaybackContext,
    PlaybackEffect,
    decide_playback,
)
from nahormaar_backend.domain.models import (
    HISTORY_LIMIT,
    HistoryEntry,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
    VoiceState,
)
from nahormaar_backend.persistence.player_store import SQLiteStore


@pytest.mark.parametrize("clear_checkpoint", [False, True])
def test_lifecycle_commits_exact_restart_intent_and_receipt(
    player: Player, store: SQLiteStore, clear_checkpoint: bool
) -> None:
    entry = QueueEntry("track")
    checkpoint = PlaybackCheckpoint(7, entry.id, 35, True, 0.4, history_recorded=True)
    player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.PAUSED, entry), checkpoint, record_history=True
    )
    before = player.snapshot
    revisions = player.revisions
    receipt = Receipt(uuid4(), "lifecycle")
    assert player.reserve(receipt) is None
    completed = replace(receipt, outcome=Outcome())
    target = replace(before, state=PlaybackState.ERROR)
    expected_checkpoint = None if clear_checkpoint else checkpoint

    result = player.apply_request(
        completed, lambda value: value.commit_lifecycle(target, expected_checkpoint)
    )

    assert result == player.snapshot == store.load() == target
    assert player.checkpoint == store.checkpoint() == expected_checkpoint
    assert result.recently_played == before.recently_played
    assert (
        player.revisions
        == store.revisions()
        == Revisions(revisions.revision + 1, revisions.queue_revision)
    )
    assert player.reserve(receipt) == completed


def test_checkpoint_only_commit_is_durable_and_unchanged_commit_is_a_noop(
    player: Player, store: SQLiteStore
) -> None:
    entry = QueueEntry("track")
    snapshot = PlayerSnapshot(PlaybackState.PLAYING, entry)
    checkpoint = PlaybackCheckpoint(7, entry.id, 20)
    player.commit_lifecycle(snapshot, checkpoint)
    revisions = player.revisions
    changed = replace(
        checkpoint, position_seconds=42.5, volume=0.35, history_recorded=True
    )

    assert player.commit_lifecycle(snapshot, changed) == snapshot
    assert store.load() == snapshot
    assert store.checkpoint() == changed
    assert (
        player.revisions
        == store.revisions()
        == Revisions(revisions.revision + 1, revisions.queue_revision)
    )
    committed_revisions = player.revisions
    receipt = Receipt(uuid4(), "already-applied")
    assert player.reserve(receipt) is None
    completed = replace(receipt, outcome=Outcome())
    player.apply_request(
        completed, lambda value: value.commit_lifecycle(snapshot, changed)
    )
    assert player.revisions == store.revisions() == committed_revisions
    assert player.reserve(receipt) == completed


def test_lifecycle_history_records_explicit_starts_even_for_the_same_entry(
    player: Player, store: SQLiteStore
) -> None:
    entry = QueueEntry("track")
    previous = tuple(
        HistoryEntry(entry, datetime(2026, 1, 1, tzinfo=UTC))
        for _ in range(HISTORY_LIMIT)
    )
    snapshot = PlayerSnapshot(PlaybackState.PLAYING, entry, recently_played=previous)
    checkpoint = PlaybackCheckpoint(7, entry.id)
    player.commit_lifecycle(snapshot, checkpoint)
    before = datetime.now(UTC)

    first = player.commit_lifecycle(snapshot, checkpoint, record_history=True)
    second = player.commit_lifecycle(first, checkpoint, record_history=True)

    assert second == store.load()
    assert len(second.recently_played) == HISTORY_LIMIT
    assert second.recently_played[1] == first.recently_played[0]
    assert second.recently_played[2:] == previous[:-2]
    assert second.recently_played[0].entry == entry
    assert second.recently_played[0].id != second.recently_played[1].id
    assert before <= second.recently_played[0].played_at <= datetime.now(UTC)
    assert second.recently_played[0].played_at.tzinfo is UTC
    assert player.commit_lifecycle(second, checkpoint) == second


def test_lifecycle_cannot_record_history_without_a_current_entry(
    player: Player, store: SQLiteStore
) -> None:
    snapshot = PlayerSnapshot(upcoming=(QueueEntry("track"),))
    assert player.commit_lifecycle(snapshot, None, record_history=True) == snapshot
    assert store.load() == snapshot
    assert store.load().recently_played == ()


def test_failed_lifecycle_commit_preserves_snapshot_history_checkpoint_and_receipt(
    player: Player, store: SQLiteStore, tmp_path: Path
) -> None:
    first, second = QueueEntry("first"), QueueEntry("second")
    checkpoint = PlaybackCheckpoint(7, first.id, 35, False, 0.4, history_recorded=True)
    before = player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.PLAYING, first, (second,)),
        checkpoint,
        record_history=True,
    )
    revisions = player.revisions
    receipt = Receipt(uuid4(), "confirmed-start")
    assert player.reserve(receipt) is None
    with sqlite3.connect(tmp_path / "player.sqlite3", autocommit=True) as database:
        database.executescript("""
            CREATE TRIGGER reject_checkpoint BEFORE INSERT ON playback_checkpoint
            WHEN NEW.position_seconds = 0
            BEGIN SELECT RAISE(ABORT, 'injected checkpoint failure'); END;
        """)

    with pytest.raises(StorageError, match="injected checkpoint failure"):
        player.apply_request(
            replace(receipt, outcome=Outcome()),
            lambda value: value.commit_lifecycle(
                replace(before, current=second, upcoming=()),
                replace(checkpoint, entry_id=second.id, position_seconds=0),
                record_history=True,
            ),
        )

    assert player.snapshot == store.load() == before
    assert player.snapshot.recently_played == before.recently_played
    assert player.checkpoint == store.checkpoint() == checkpoint
    assert player.revisions == store.revisions() == revisions
    assert player.reserve(receipt) == receipt


def test_failed_checkpoint_clear_does_not_publish_lifecycle_state(
    player: Player, store: SQLiteStore, tmp_path: Path
) -> None:
    entry = QueueEntry("track")
    checkpoint = PlaybackCheckpoint(7, entry.id, 35)
    before = player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.PLAYING, entry), checkpoint
    )
    revisions = player.revisions
    with sqlite3.connect(tmp_path / "player.sqlite3", autocommit=True) as database:
        database.executescript("""
            CREATE TRIGGER reject_checkpoint_clear
            BEFORE DELETE ON playback_checkpoint
            BEGIN SELECT RAISE(ABORT, 'injected clear failure'); END;
        """)

    with pytest.raises(StorageError, match="injected clear failure"):
        player.commit_lifecycle(replace(before, state=PlaybackState.PAUSED), None)

    assert player.snapshot == store.load() == before
    assert player.checkpoint == store.checkpoint() == checkpoint
    assert player.revisions == store.revisions() == revisions


def test_failed_confirmation_rolls_back_history_and_recorded_flag(
    player: Player, store: SQLiteStore, tmp_path: Path
) -> None:
    entry = QueueEntry("track")
    checkpoint = PlaybackCheckpoint(7, entry.id)
    before = player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.LOADING, entry), checkpoint
    )
    revisions = player.revisions
    with sqlite3.connect(tmp_path / "player.sqlite3", autocommit=True) as database:
        database.executescript("""
            CREATE TRIGGER reject_confirmation BEFORE INSERT ON playback_checkpoint
            WHEN NEW.history_recorded = 1
            BEGIN SELECT RAISE(ABORT, 'injected confirmation failure'); END;
        """)

    with pytest.raises(StorageError, match="injected confirmation failure"):
        player.commit_lifecycle(
            replace(before, state=PlaybackState.PLAYING),
            replace(checkpoint, history_recorded=True),
            record_history=True,
        )

    assert player.snapshot == store.load() == before
    assert player.checkpoint == store.checkpoint() == checkpoint
    assert player.revisions == store.revisions() == revisions
    assert not player.snapshot.recently_played


def test_crossfade_reserves_enriched_entry_without_recording_a_start(
    player: Player, store: SQLiteStore
) -> None:
    first, second = QueueEntry("first"), QueueEntry("second")
    before = player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.PLAYING, first, (second,)),
        PlaybackCheckpoint(7, first.id, 35, history_recorded=True),
        record_history=True,
    )

    decision = decide_playback(
        before,
        PlaybackContext(
            entry_id=first.id,
            attempt_id=uuid4(),
            started=True,
            history_recorded=True,
        ),
        LifecycleEvent.CROSSFADE,
    )
    assert PlaybackEffect.RECORD_HISTORY not in decision.effects
    player.commit_lifecycle(decision.snapshot, PlaybackCheckpoint(7, second.id))
    reserved = player.enrich(second.id, TrackMetadata(title="Second track"))

    assert reserved.state is PlaybackState.LOADING
    assert reserved.current == replace(second, title="Second track")
    assert reserved.upcoming == ()
    assert reserved.recently_played == before.recently_played
    assert store.load() == reserved
    assert store.checkpoint() == PlaybackCheckpoint(7, second.id)


@pytest.mark.parametrize(
    ("state", "paused"),
    [
        (PlaybackState.PLAYING, False),
        (PlaybackState.PAUSED, True),
        (PlaybackState.LOADING, True),
        (PlaybackState.ERROR, True),
    ],
)
@pytest.mark.parametrize("history_recorded", [False, True])
def test_startup_keeps_saved_position_pause_volume_and_history(
    store: SQLiteStore, state: PlaybackState, paused: bool, history_recorded: bool
) -> None:
    entry = QueueEntry("track")
    history = (HistoryEntry(entry, datetime(2026, 1, 1, tzinfo=UTC)),)
    stored = PlayerSnapshot(state, entry, recently_played=history)
    checkpoint = PlaybackCheckpoint(
        7, entry.id, 35.25, paused, 0.4, history_recorded=history_recorded
    )
    store.save(stored, checkpoint=checkpoint)

    player = Player(store)

    assert (
        player.snapshot
        == store.load()
        == replace(
            stored, state=PlaybackState.LOADING, voice_state=VoiceState.DISCONNECTED
        )
    )
    assert player.checkpoint == store.checkpoint() == checkpoint
    assert player.snapshot.recently_played == history
