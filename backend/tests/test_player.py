# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.application.player import Player
from nahormaar_backend.application.recovery import reconcile_checkpoint
from nahormaar_backend.domain import commands, queue
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.fsm import (
    InvalidTransitionError,
    LifecycleEvent,
    PlaybackContext,
    PlaybackEffect,
    decide_playback,
)
from nahormaar_backend.domain.models import PlaybackState, PlayerSnapshot, QueueEntry
from nahormaar_backend.persistence.player_store import SQLiteStore


def test_repeated_videos_are_independent_entries(
    player: Player, store: SQLiteStore
) -> None:
    first = QueueEntry("https://youtu.be/same", video_id="same")
    second = QueueEntry("https://youtu.be/same", video_id="same")
    player.enqueue(first)
    before = player.snapshot
    player.enqueue(second)
    assert first.id != second.id
    assert before.upcoming == (first,)
    assert player.snapshot.state is PlaybackState.IDLE
    player.remove((first,))
    assert player.snapshot.upcoming == (second,)
    assert store.load() == player.snapshot


def test_duplicate_entry_id_is_rejected(player: Player, store: SQLiteStore) -> None:
    entry = QueueEntry("https://youtu.be/same")
    player.enqueue(entry)
    before = player.snapshot
    with pytest.raises(ValueError, match="unique"):
        player.enqueue(entry)
    assert player.snapshot == store.load() == before


def test_reorder_uses_ids_and_preserves_other_entries(
    player: Player, store: SQLiteStore
) -> None:
    first, second, third = (QueueEntry(f"https://youtu.be/{i}") for i in range(3))
    for entry in (first, second, third):
        player.enqueue(entry)
    player.move_before(third.id, first.id)
    assert player.snapshot.upcoming == (third, first, second)
    player.move_before(first.id)
    assert player.snapshot.upcoming == (third, second, first)
    assert player.move_before(second.id, second.id) == store.load()


def test_unknown_ids_and_current_entry_cannot_be_edited(
    player: Player, store: SQLiteStore
) -> None:
    first, second = (QueueEntry(f"https://youtu.be/{i}") for i in range(2))
    player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.LOADING, first, (second,)), None
    )
    before = player.snapshot
    with pytest.raises(KeyError):
        queue.select_removal(before, commands.Remove(uuid4()))
    with pytest.raises(KeyError):
        player.move_before(uuid4())
    with pytest.raises(KeyError):
        player.move_before(second.id, uuid4())
    with pytest.raises(ValueError, match="current entry"):
        queue.select_removal(before, commands.Remove(first.id))
    with pytest.raises(ValueError, match="current entry"):
        player.move_before(first.id)
    with pytest.raises(ValueError, match="current entry"):
        player.move_before(second.id, first.id)
    assert player.snapshot == store.load() == before


def test_clear_keeps_current_and_history(player: Player, store: SQLiteStore) -> None:
    first, second = (QueueEntry(f"https://youtu.be/{i}") for i in range(2))
    player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.PLAYING, first, (second,)),
        None,
        record_history=True,
    )
    history = player.snapshot.recently_played
    player.remove(queue.select_removal(player.snapshot, commands.Clear(0)))
    assert player.snapshot.current == first
    assert player.snapshot.state is PlaybackState.PLAYING
    assert player.snapshot.upcoming == ()
    assert player.snapshot.recently_played == history
    assert store.load() == player.snapshot


def test_lifecycle_decisions_commit_state_and_history(
    player: Player, store: SQLiteStore
) -> None:
    first, second = (QueueEntry(f"https://youtu.be/{i}") for i in range(2))
    player.enqueue(first)
    player.enqueue(second)
    context = PlaybackContext()
    for event, state, current, history_count in (
        (LifecycleEvent.PLAY, PlaybackState.LOADING, first, 0),
        (LifecycleEvent.STARTED, PlaybackState.PLAYING, first, 1),
        (LifecycleEvent.PAUSE, PlaybackState.PAUSED, first, 1),
        (LifecycleEvent.PLAY, PlaybackState.PLAYING, first, 1),
        (LifecycleEvent.SKIP, PlaybackState.LOADING, second, 1),
        (LifecycleEvent.STARTED, PlaybackState.PLAYING, second, 2),
        (LifecycleEvent.STOP, PlaybackState.IDLE, None, 2),
        (LifecycleEvent.PLAY, PlaybackState.LOADING, second, 2),
        (LifecycleEvent.SKIP, PlaybackState.IDLE, None, 2),
    ):
        decision = decide_playback(
            player.snapshot,
            context,
            event,
            attempt_id=context.attempt_id,
        )
        player.commit_lifecycle(
            decision.snapshot,
            None,
            record_history=PlaybackEffect.RECORD_HISTORY in decision.effects,
        )
        context = decision.context
        if PlaybackEffect.LOAD in decision.effects:
            context = replace(context, attempt_id=uuid4())
        assert player.snapshot.state is state
        assert player.snapshot.current == current
        assert len(player.snapshot.recently_played) == history_count
        assert store.load() == player.snapshot
        if event is LifecycleEvent.STOP:
            assert player.snapshot.upcoming == (second,)


def test_invalid_transition_is_not_saved(player: Player, store: SQLiteStore) -> None:
    player.enqueue(QueueEntry("https://youtu.be/example"))
    before = player.snapshot
    revisions = player.revisions
    with pytest.raises(InvalidTransitionError):
        decide_playback(before, PlaybackContext(), LifecycleEvent.PAUSE)
    assert player.snapshot == store.load() == before
    assert player.revisions == store.revisions() == revisions


def test_empty_queue_controls(player: Player, store: SQLiteStore) -> None:
    for event in (LifecycleEvent.PLAY, LifecycleEvent.SKIP, LifecycleEvent.STOP):
        decision = decide_playback(player.snapshot, PlaybackContext(), event)
        assert player.commit_lifecycle(decision.snapshot, None) == PlayerSnapshot()
    assert player.remove(()) == store.load() == PlayerSnapshot()


@pytest.mark.parametrize(
    ("state", "same_entry", "paused", "expected_position", "expected_paused"),
    [
        (PlaybackState.LOADING, True, True, 35, True),
        (PlaybackState.LOADING, True, False, 35, False),
        (PlaybackState.PLAYING, True, True, 35, False),
        (PlaybackState.PAUSED, True, False, 35, True),
        (PlaybackState.LOADING, False, True, 0, False),
        (PlaybackState.PAUSED, False, False, 0, True),
        (PlaybackState.IDLE, False, True, 0, False),
    ],
)
def test_checkpoint_reconciliation_preserves_existing_policy(
    state: PlaybackState,
    same_entry: bool,
    paused: bool,
    expected_position: float,
    expected_paused: bool,
) -> None:
    previous_id = UUID(int=1)
    current = (
        QueueEntry("track", id=previous_id if same_entry else UUID(int=2))
        if state is not PlaybackState.IDLE
        else None
    )
    checkpoint = PlaybackCheckpoint(7, previous_id, 35, paused, 0.4)
    assert reconcile_checkpoint(PlayerSnapshot(state, current), checkpoint) == (
        PlaybackCheckpoint(
            7,
            current.id if current else None,
            expected_position,
            expected_paused,
            0.4,
        )
    )
    assert checkpoint == PlaybackCheckpoint(7, previous_id, 35, paused, 0.4)


def test_checkpoint_reconciliation_does_not_create_restart_intent() -> None:
    current = QueueEntry("track")
    for state in PlaybackState:
        snapshot = PlayerSnapshot(
            state, current if state is not PlaybackState.IDLE else None
        )
        assert reconcile_checkpoint(snapshot, None) is None
    assert (
        reconcile_checkpoint(
            PlayerSnapshot(PlaybackState.ERROR, current),
            PlaybackCheckpoint(7, current.id, 35),
        )
        is None
    )
