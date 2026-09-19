# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from uuid import uuid4

import pytest

from nahormaar_backend.fsm import InvalidTransitionError
from nahormaar_backend.models import PlaybackState, PlayerSnapshot, QueueEntry
from nahormaar_backend.player import Player
from nahormaar_backend.storage import SQLiteStore


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
    player.remove(first.id)
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
    player.enqueue(first)
    player.enqueue(second)
    player.play()
    before = player.snapshot
    with pytest.raises(KeyError):
        player.remove(uuid4())
    with pytest.raises(KeyError):
        player.move_before(uuid4())
    with pytest.raises(KeyError):
        player.move_before(second.id, uuid4())
    with pytest.raises(ValueError, match="current entry"):
        player.remove(first.id)
    with pytest.raises(ValueError, match="current entry"):
        player.move_before(first.id)
    with pytest.raises(ValueError, match="current entry"):
        player.move_before(second.id, first.id)
    assert player.snapshot == store.load() == before


def test_clear_keeps_current_but_stop_requeues_it(
    player: Player, store: SQLiteStore
) -> None:
    first, second = (QueueEntry(f"https://youtu.be/{i}") for i in range(2))
    player.enqueue(first)
    player.enqueue(second)
    player.play()
    player.mark_playing()
    player.clear()
    assert player.snapshot.current == first
    assert player.snapshot.state is PlaybackState.PLAYING
    assert player.snapshot.upcoming == ()
    history = player.snapshot.recently_played
    player.stop()
    assert player.snapshot == PlayerSnapshot(upcoming=(first,), recently_played=history)
    assert store.load() == player.snapshot
    assert player.play().state is PlaybackState.LOADING


def test_player_controls_follow_fsm(player: Player, store: SQLiteStore) -> None:
    first, second = (QueueEntry(f"https://youtu.be/{i}") for i in range(2))
    player.enqueue(first)
    player.enqueue(second)
    player.play()
    assert player.fail().state is PlaybackState.ERROR
    player.play()
    player.mark_playing()
    assert player.pause().state is PlaybackState.PAUSED
    assert player.play().state is PlaybackState.PLAYING
    assert player.skip().current == second
    assert player.skip() == PlayerSnapshot(
        recently_played=player.snapshot.recently_played
    )
    assert store.load() == player.snapshot


def test_invalid_transition_is_not_saved(player: Player, store: SQLiteStore) -> None:
    player.enqueue(QueueEntry("https://youtu.be/example"))
    player.play()
    before = player.snapshot
    with pytest.raises(InvalidTransitionError):
        player.pause()
    assert player.snapshot == store.load() == before


def test_empty_queue_controls(player: Player, store: SQLiteStore) -> None:
    assert player.play() == PlayerSnapshot()
    assert player.skip() == PlayerSnapshot()
    assert player.stop() == PlayerSnapshot()
    assert player.clear() == store.load() == PlayerSnapshot()
