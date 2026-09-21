# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import replace
from uuid import uuid4

import pytest

from nahormaar_backend.engine.domain.queue import (
    Add,
    Clear,
    Move,
    Queue,
    QueueEntry,
    Remove,
    Undo,
    UndoUnavailable,
)
from nahormaar_backend.engine.domain.radio import (
    ManualStrategy,
    RadioLoaded,
    RadioState,
    RadioStrategy,
)

from .test_catalog import TIME, REFERENCE
from .test_session import ACTOR


@pytest.mark.parametrize("reverse_anchors", [False, True])
def test_undo_of_disjoint_groups_preserves_later_edits(reverse_anchors: bool) -> None:
    queue = Queue(uuid4())
    queue, added, _ = queue.edit(
        Add(tuple(uuid4() for _ in range(4))), actor=ACTOR, now=TIME
    )
    first, removed, third, last = added.entries
    queue, _, removal = queue.edit(Remove(removed.id), actor=ACTOR, now=TIME)
    assert removal is not None
    if reverse_anchors:
        queue, _, _ = queue.edit(Move(third.id, first.id, 0), actor=ACTOR, now=TIME)
    else:
        queue, _, _ = queue.edit(Remove(first.id), actor=ACTOR, now=TIME)
    restored, outcome, _ = queue.edit(
        Undo(removal.id), actor=ACTOR, now=TIME, removal=removal
    )
    expected = (
        (third.id, first.id, last.id, removed.id)
        if reverse_anchors
        else (removed.id, third.id, last.id)
    )
    assert tuple(entry.id for entry in restored.entries) == expected
    assert outcome.restored_count == 1
    with pytest.raises(UndoUnavailable):
        restored.edit(Undo(removal.id), actor=ACTOR, now=TIME, removal=removal)


def test_clear_undo_appends_to_later_entries_without_replacing_them() -> None:
    queue = Queue(uuid4())
    queue, added, _ = queue.edit(Add((uuid4(), uuid4())), actor=ACTOR, now=TIME)
    cleared, _, removal = queue.edit(Clear(0), actor=ACTOR, now=TIME)
    assert removal is not None
    newer, inserted, _ = cleared.edit(Add((uuid4(),)), actor=ACTOR, now=TIME)
    restored, _, _ = newer.edit(
        Undo(removal.id), actor=ACTOR, now=TIME, removal=removal
    )
    assert tuple(entry.id for entry in restored.entries) == (
        inserted.entries[0].id,
        *(entry.id for entry in added.entries),
    )
    assert tuple(entry.position for entry in restored.entries) == (0, 1, 2)


def test_queue_identity_and_batch_validation_and_current_track_duplicate_filter() -> (
    None
):
    queue = Queue(uuid4())
    track = uuid4()
    queue, outcome, _ = queue.edit(
        Add((track,), True), actor=ACTOR, now=TIME, current_track_id=track
    )
    assert not queue.entries and outcome.skipped_count == 1
    for count in (0, 101):
        with pytest.raises(ValueError):
            queue.edit(Add(tuple(uuid4() for _ in range(count))), actor=ACTOR, now=TIME)
    entry = QueueEntry(queue.session_id, track, 9)
    assert Queue(queue.session_id, (entry,)).entries[0].position == 0
    with pytest.raises(ValueError, match="unique"):
        Queue(queue.session_id, (entry, entry))
    with pytest.raises(ValueError, match="belong"):
        Queue(uuid4(), (entry,))


def test_radio_policy_preserves_pool_when_paused_and_rejects_stale_requests() -> None:
    manual = ManualStrategy()
    assert manual.queue_next((), frozenset(), paused=False) == (manual, (), None)
    track = uuid4()
    radio = RadioStrategy(REFERENCE, ACTOR, pool=(track, track))
    assert radio.queue_next((), frozenset(), paused=True) == (radio, (), None)
    loading, selected, request = radio.queue_next((), frozenset(), paused=False)
    assert selected == (track,) and request is not None
    assert loading.queue_next((), frozenset(), paused=False) == (loading, (), None)
    message = RadioLoaded(radio.generation, request.id, (track,))
    assert loading.loaded(replace(message, request_id=uuid4()), frozenset()) == loading
    assert loading.loaded(replace(message, generation=uuid4()), frozenset()) == loading
    exhausted = loading.loaded(message, frozenset((track,)))
    assert exhausted.state is RadioState.WAITING
    assert exhausted.queue_next((), frozenset(), paused=False) == (exhausted, (), None)
