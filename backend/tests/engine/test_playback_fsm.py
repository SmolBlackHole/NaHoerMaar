# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.engine.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioProgress,
    AudioStarted,
    CrossfadeCompleted,
    CrossfadeDue,
    VoiceConnection,
    VoiceDisconnected,
)
from nahormaar_backend.engine.domain.playback import (
    Activate,
    AttemptFailed,
    Connect,
    Control,
    Joined,
    Join,
    PlaybackMessage,
    Prepared,
    Seek,
    SetCrossfade,
    SourceResolved,
    StartAttempt,
    TransitionFailed,
    decide,
)
from nahormaar_backend.engine.domain.queue import Queue, QueueEntry
from nahormaar_backend.engine.domain.radio import RadioStrategy
from nahormaar_backend.engine.domain.sessions import (
    ListeningSession,
    PlaybackCheckpoint,
    PlaybackEndReason,
    PlaybackIntent,
    PlaybackPhase,
    PlaybackRuntime,
    SessionSnapshot,
)

from .test_catalog import REFERENCE, TIME
from .test_session import ACTOR


def initial(*, connected: bool = True, count: int = 3) -> SessionSnapshot:
    settings = ListeningSession(channel_id=123)
    return SessionSnapshot(
        settings,
        Queue(
            settings.id,
            tuple(QueueEntry(settings.id, uuid4(), i, ACTOR) for i in range(count)),
        ),
        PlaybackCheckpoint(settings.id),
        playback=PlaybackRuntime(connection_id=uuid4() if connected else None),
    )


def apply(state: SessionSnapshot, message: PlaybackMessage) -> SessionSnapshot:
    connection = (
        VoiceConnection(state.playback.connection_id, 123)
        if state.playback.connection_id
        else None
    )
    return decide(state, message, now=TIME, connection=connection).snapshot


def attempt(state: SessionSnapshot) -> UUID:
    assert state.playback.attempt_id is not None
    return state.playback.attempt_id


def playing(state: SessionSnapshot | None = None) -> SessionSnapshot:
    state = apply(state or initial(), Control.PLAY)
    state = apply(state, SourceResolved(attempt(state), 120))
    return apply(state, AudioStarted(attempt(state), 0.02))


def test_selection_confirmation_seek_and_retry_have_one_logical_play() -> None:
    state = initial()
    original = state.queue.entries[0]
    state = apply(state, Control.PLAY)
    assert len(state.queue.entries) == 2 and not state.history
    assert state.checkpoint.entry_id == original.id
    state = apply(state, SourceResolved(attempt(state), 120))
    assert not state.history
    state = apply(state, AudioStarted(attempt(state), 0.02))
    record = state.history[0]
    assert record.entry_id == original.id and record.added_by == ACTOR
    old_attempt = attempt(state)
    state = apply(state, Seek(45))
    assert attempt(state) != old_attempt and state.checkpoint.play_id == record.id
    assert (
        apply(state, AudioCompleted(old_attempt, 120, AudioEndReason.NATURAL)) == state
    )
    state = apply(state, SourceResolved(attempt(state), 120))
    state = apply(state, AudioStarted(attempt(state), 45.02))
    assert len(state.history) == 1
    assert apply(state, AudioStarted(attempt(state), 0.02)) == state
    state = apply(state, AudioCompleted(attempt(state), 50, AudioEndReason.INTERRUPTED))
    assert state.playback.retries == 1 and state.checkpoint.position_seconds == 50
    assert state.checkpoint.play_id == record.id and len(state.queue.entries) == 2
    state = apply(state, AudioCompleted(attempt(state), 51, AudioEndReason.INTERRUPTED))
    assert state.checkpoint.entry_id != original.id and len(state.queue.entries) == 1
    assert state.history[0].end_reason is PlaybackEndReason.FAILED


@pytest.mark.parametrize("paused", [False, True])
def test_transport_loss_wins_over_completion_and_join_resumes(paused: bool) -> None:
    state = playing()
    if paused:
        state = apply(state, Control.PAUSE)
    old_connection = state.playback.connection_id
    assert old_connection is not None
    lost = decide(
        state,
        AudioCompleted(attempt(state), 36, AudioEndReason.INTERRUPTED),
        now=TIME,
        connection=None,
    ).snapshot
    assert lost.queue == state.queue and lost.history == state.history
    assert lost.checkpoint.position_seconds == 36
    assert lost.checkpoint.intent is (
        PlaybackIntent.PAUSED if paused else PlaybackIntent.PLAYING
    )
    assert lost.playback.phase is PlaybackPhase.SUSPENDED
    joined = apply(lost, Join(456))
    assert joined.playback.joining_id
    ready = apply(joined, Joined(VoiceConnection(joined.playback.joining_id, 456)))
    assert ready.checkpoint == lost.checkpoint and ready.queue == lost.queue
    assert ready.playback.phase is PlaybackPhase.RESOLVING
    stale = VoiceDisconnected(
        VoiceConnection(old_connection, 123), AudioProgress(attempt(state), 0)
    )
    assert apply(ready, stale) == ready


def test_stop_requeues_occurrence_but_leave_keeps_resume_checkpoint() -> None:
    state = playing()
    stopped = apply(state, Control.STOP)
    assert stopped.checkpoint.intent is PlaybackIntent.STOPPED
    assert stopped.queue.entries[0].id == state.checkpoint.entry_id
    assert stopped.queue.entries[0].added_by == ACTOR
    assert stopped.history[0].end_reason is PlaybackEndReason.STOPPED
    assert stopped.playback.connection_id == state.playback.connection_id
    replay = playing(stopped)
    assert replay.checkpoint.play_id != state.checkpoint.play_id
    left = apply(state, Control.LEAVE)
    assert left.checkpoint == state.checkpoint and left.history == state.history
    assert left.settings.channel_id is None and left.playback.connection_id is None


def test_startup_restore_does_not_start_stopped_queue_but_explicit_join_does() -> None:
    state = initial(connected=False)
    restored = decide(state, Control.RESTORE, now=TIME)
    assert len(restored.effects) == 1 and isinstance(restored.effects[0], Connect)
    ready = apply(
        restored.snapshot,
        Joined(VoiceConnection(restored.effects[0].connection_id, 123)),
    )
    assert (
        ready.queue == state.queue and ready.checkpoint.intent is PlaybackIntent.STOPPED
    )
    explicit = apply(ready, Join(123))
    assert explicit.checkpoint.entry_id == state.queue.entries[0].id
    assert explicit.playback.phase is PlaybackPhase.RESOLVING


def test_only_radio_continues_after_empty_queue_receives_entries() -> None:
    manual = playing(initial(count=1))
    radio = replace(manual, strategy=RadioStrategy(REFERENCE, ACTOR))
    for original, continues in [(manual, False), (radio, True)]:
        ended = apply(
            original, AudioCompleted(attempt(original), 120, AudioEndReason.NATURAL)
        )
        assert ended.playback.waiting_for_queue is continues
        entry = QueueEntry(ended.settings.id, uuid4(), 0)
        replenished = apply(
            replace(ended, queue=Queue(ended.settings.id, (entry,))), Control.RECONCILE
        )
        assert (replenished.checkpoint.entry_id == entry.id) is continues


def test_crossfade_keeps_tail_history_and_ignores_outgoing_completion() -> None:
    state = playing()
    state = apply(state, SetCrossfade(5))
    preparation = state.playback.preparation
    assert preparation is not None
    outgoing_attempt, outgoing_record = attempt(state), state.history[0]
    # Due can reach the inbox before the preparation acknowledgement.
    fade = decide(state, CrossfadeDue(outgoing_attempt, preparation.id, 180), now=TIME)
    assert any(isinstance(effect, Activate) for effect in fade.effects)
    state = fade.snapshot
    assert state.playback.duration_seconds == 180
    assert state.playback.outgoing_play_id == outgoing_record.id
    assert state.checkpoint.play_id is None and len(state.history) == 1
    assert (
        apply(state, AudioCompleted(outgoing_attempt, 120, AudioEndReason.NATURAL))
        == state
    )
    assert apply(state, Prepared(preparation.id, True)) == state
    state = apply(state, AudioStarted(attempt(state), 0.02))
    assert len(state.history) == 2 and all(
        record.ended_at is None for record in state.history
    )
    completed = decide(
        state,
        CrossfadeCompleted(attempt(state), preparation.id),
        now=TIME + timedelta(seconds=5),
    ).snapshot
    assert completed.history[1].end_reason is PlaybackEndReason.COMPLETED
    assert completed.history[0].ended_at is None


def test_failed_transition_restarts_same_unconfirmed_entry_not_next_one() -> None:
    state = apply(playing(), SetCrossfade(5))
    assert state.playback.preparation
    preparation = state.playback.preparation.id
    state = apply(state, CrossfadeDue(attempt(state), preparation, 120))
    current = state.checkpoint.entry_id
    failed = decide(state, TransitionFailed(attempt(state), preparation), now=TIME)
    assert failed.snapshot.checkpoint.entry_id == current
    assert failed.snapshot.queue == state.queue
    assert any(isinstance(effect, StartAttempt) for effect in failed.effects)


@pytest.mark.parametrize("seconds", [-1, 120, float("nan"), float("inf")])
def test_invalid_seek_does_not_change_state(seconds: float) -> None:
    state = playing()
    result = decide(
        state,
        Seek(seconds),
        now=TIME,
        connection=VoiceConnection(state.playback.connection_id or uuid4(), 123),
    )
    assert (
        result.code == "invalid_seek"
        and result.snapshot == state
        and not result.effects
    )


def test_resolution_failure_during_disconnect_does_not_skip() -> None:
    state = apply(initial(), Control.PLAY)
    result = decide(state, AttemptFailed(attempt(state)), now=TIME, connection=None)
    assert result.snapshot.queue == state.queue
    assert result.snapshot.checkpoint == state.checkpoint
    assert result.snapshot.playback.phase is PlaybackPhase.SUSPENDED


def test_pause_wins_over_already_queued_crossfade_due_and_natural_end() -> None:
    state = apply(playing(), SetCrossfade(5))
    assert state.playback.preparation
    due = CrossfadeDue(attempt(state), state.playback.preparation.id, 120)
    paused = apply(state, Control.PAUSE)
    late = apply(paused, due)
    assert late.checkpoint == paused.checkpoint and late.queue == paused.queue
    assert late.playback.preparation is None
    completed = apply(late, AudioCompleted(attempt(late), 120, AudioEndReason.NATURAL))
    assert completed.checkpoint.intent is PlaybackIntent.PAUSED
    assert completed.checkpoint.entry_id == late.queue.entries[0].id
