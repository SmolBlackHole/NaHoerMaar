# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import replace
from uuid import uuid4

import pytest

from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.fsm import (
    InvalidTransitionError,
    LifecycleEvent,
    PlaybackContext,
    PlaybackEffect,
    decide_playback,
)
from nahormaar_backend.domain.models import (
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    VoiceState,
)


def _snapshot(state: PlaybackState, *, has_next: bool = True) -> PlayerSnapshot:
    current = (
        None if state is PlaybackState.IDLE else QueueEntry("https://youtu.be/first")
    )
    upcoming = (QueueEntry("https://youtu.be/next"),) if has_next else ()
    return PlayerSnapshot(state, current, upcoming, VoiceState.CONNECTED)


def _context(snapshot: PlayerSnapshot) -> PlaybackContext:
    return PlaybackContext(
        entry_id=snapshot.current.id if snapshot.current else None,
        attempt_id=uuid4() if snapshot.current else None,
        position_seconds=45 if snapshot.current else 0,
        paused=snapshot.state is PlaybackState.PAUSED,
        started=snapshot.state in (PlaybackState.PLAYING, PlaybackState.PAUSED),
        history_recorded=snapshot.state
        in (PlaybackState.PLAYING, PlaybackState.PAUSED),
        channel_id=7,
        rejoin=True,
    )


def test_start_pause_before_confirmation_and_resume_keep_the_same_entry() -> None:
    idle = _snapshot(PlaybackState.IDLE)
    loading = decide_playback(idle, _context(idle), LifecycleEvent.PLAY)
    assert loading.snapshot.current == idle.upcoming[0]
    assert loading.snapshot.state is PlaybackState.LOADING
    assert loading.snapshot.upcoming == ()
    assert loading.effects == (PlaybackEffect.STOP_OUTPUT, PlaybackEffect.LOAD)
    attempt = uuid4()
    context = replace(loading.context, attempt_id=attempt)
    paused = decide_playback(loading.snapshot, context, LifecycleEvent.PAUSE)
    assert paused.snapshot.state is PlaybackState.PAUSED
    started = decide_playback(
        paused.snapshot, paused.context, LifecycleEvent.STARTED, attempt_id=attempt
    )
    assert started.snapshot.state is PlaybackState.PAUSED
    assert started.effects == (PlaybackEffect.RECORD_HISTORY,)
    resumed = decide_playback(started.snapshot, started.context, LifecycleEvent.PLAY)
    assert resumed.snapshot.state is PlaybackState.PLAYING
    assert resumed.snapshot.current == idle.upcoming[0]
    assert resumed.effects == (PlaybackEffect.RESUME_OUTPUT,)
    assert idle.current is None


@pytest.mark.parametrize("state", list(PlaybackState))
def test_seek_preserves_active_track_and_logical_play(state: PlaybackState) -> None:
    before = _snapshot(state)
    context = _context(before)
    if state in (PlaybackState.PLAYING, PlaybackState.PAUSED):
        after = decide_playback(
            before, context, LifecycleEvent.SEEK, position_seconds=12
        )
        assert after.snapshot.current == before.current
        assert after.snapshot.upcoming == before.upcoming
        assert after.context.position_seconds == 12
        assert after.context.history_recorded
        assert after.context.paused == context.paused
        assert after.context.attempt_id is None
        assert after.effects == (PlaybackEffect.STOP_OUTPUT, PlaybackEffect.LOAD)
    else:
        with pytest.raises(InvalidTransitionError):
            decide_playback(before, context, LifecycleEvent.SEEK)


@pytest.mark.parametrize("state", list(PlaybackState))
def test_recovery_without_checkpoint_requeues_once(state: PlaybackState) -> None:
    before = _snapshot(state)
    after = decide_playback(before, _context(before), LifecycleEvent.RECOVER)
    expected = ((before.current,) if before.current else ()) + before.upcoming
    assert after.snapshot == PlayerSnapshot(upcoming=expected)
    assert after.context == PlaybackContext()
    assert after.effects == ()
    assert (
        decide_playback(after.snapshot, after.context, LifecycleEvent.RECOVER) == after
    )


@pytest.mark.parametrize("paused", [False, True])
def test_recovery_retains_checkpoint_until_connection_is_restored(paused: bool) -> None:
    before = _snapshot(PlaybackState.PAUSED if paused else PlaybackState.PLAYING)
    assert before.current
    checkpoint = PlaybackCheckpoint(7, before.current.id, 45, paused, 0.4, True)
    recovered = decide_playback(
        before, PlaybackContext(), LifecycleEvent.RECOVER, checkpoint=checkpoint
    )
    assert recovered.snapshot.current == before.current
    assert recovered.snapshot.upcoming == before.upcoming
    assert recovered.snapshot.state is PlaybackState.LOADING
    assert recovered.snapshot.voice_state is VoiceState.DISCONNECTED
    assert recovered.effects == ()
    joined = decide_playback(
        recovered.snapshot, recovered.context, LifecycleEvent.RESTORED, channel_id=7
    )
    assert joined.context.position_seconds == 45
    assert joined.context.history_recorded
    assert joined.context.paused is paused
    assert joined.effects == (PlaybackEffect.STOP_OUTPUT, PlaybackEffect.LOAD)


@pytest.mark.parametrize("state", list(PlaybackState))
def test_stop_requeues_and_resets_play_but_keeps_connection(
    state: PlaybackState,
) -> None:
    before = _snapshot(state)
    after = decide_playback(before, _context(before), LifecycleEvent.STOP)
    assert after.snapshot.state is PlaybackState.IDLE
    assert after.snapshot.current is None
    assert after.snapshot.upcoming == (
        ((before.current,) if before.current else ()) + before.upcoming
    )
    assert after.snapshot.voice_state is VoiceState.CONNECTED
    assert after.context == PlaybackContext(channel_id=7, rejoin=True)
    assert after.effects == (PlaybackEffect.STOP_OUTPUT,)


@pytest.mark.parametrize("has_next", [True, False])
@pytest.mark.parametrize("state", list(PlaybackState))
def test_skip_advances_only_an_active_player(
    state: PlaybackState, has_next: bool
) -> None:
    before = _snapshot(state, has_next=has_next)
    after = decide_playback(before, _context(before), LifecycleEvent.SKIP)
    if state is PlaybackState.IDLE:
        assert after.snapshot == before
    elif has_next:
        assert after.snapshot.current == before.upcoming[0]
        assert after.snapshot.state is PlaybackState.LOADING
        assert after.snapshot.upcoming == ()
    else:
        assert after.snapshot.current is None
        assert after.snapshot.state is PlaybackState.IDLE


def test_empty_queue_play_and_restored_connection_stay_idle() -> None:
    empty = PlayerSnapshot()
    after = decide_playback(empty, PlaybackContext(), LifecycleEvent.PLAY)
    assert after.snapshot == empty
    assert PlaybackEffect.LOAD not in after.effects
    queued = PlayerSnapshot(upcoming=(QueueEntry("queued"),))
    restored = decide_playback(
        queued, PlaybackContext(), LifecycleEvent.RESTORED, channel_id=7
    )
    assert restored.snapshot.current is None
    assert restored.snapshot.upcoming == queued.upcoming
    assert restored.effects == ()
    joined = decide_playback(
        queued, PlaybackContext(), LifecycleEvent.JOINED, channel_id=7
    )
    assert joined.snapshot.current == queued.upcoming[0]


@pytest.mark.parametrize(
    "state", [PlaybackState.IDLE, PlaybackState.PAUSED, PlaybackState.ERROR]
)
def test_pause_rejects_invalid_states(state: PlaybackState) -> None:
    before = _snapshot(state)
    with pytest.raises(InvalidTransitionError) as error:
        decide_playback(before, _context(before), LifecycleEvent.PAUSE)
    assert error.value.state is state
    assert error.value.event is LifecycleEvent.PAUSE
    assert before.state is state


@pytest.mark.parametrize("state", [PlaybackState.PLAYING, PlaybackState.PAUSED])
@pytest.mark.parametrize("has_next", [True, False])
def test_natural_completion_advances_even_if_pause_raced_with_end(
    state: PlaybackState, has_next: bool
) -> None:
    before = _snapshot(state, has_next=has_next)
    context = _context(before)
    after = decide_playback(
        before, context, LifecycleEvent.FINISHED, attempt_id=context.attempt_id
    )
    assert after.snapshot.current == (before.upcoming[0] if has_next else None)
    assert after.snapshot.state is (
        PlaybackState.LOADING if has_next else PlaybackState.IDLE
    )
    assert PlaybackEffect.REPORT_FAILURE not in after.effects


@pytest.mark.parametrize("event", [LifecycleEvent.LEAVE, LifecycleEvent.DISCONNECTED])
@pytest.mark.parametrize("paused", [False, True])
def test_disconnect_retains_position_and_join_preserves_pause(
    event: LifecycleEvent, paused: bool
) -> None:
    before = _snapshot(PlaybackState.PAUSED if paused else PlaybackState.PLAYING)
    disconnected = decide_playback(before, _context(before), event)
    assert disconnected.snapshot.current == before.current
    assert disconnected.snapshot.upcoming == before.upcoming
    assert disconnected.snapshot.voice_state is VoiceState.DISCONNECTED
    assert disconnected.context.rejoin is (event is LifecycleEvent.DISCONNECTED)
    assert disconnected.context.attempt_id is None
    joined = decide_playback(
        disconnected.snapshot, disconnected.context, LifecycleEvent.JOINED, channel_id=8
    )
    assert joined.snapshot.current == before.current
    assert joined.context.position_seconds == 45
    assert joined.context.history_recorded
    assert joined.context.channel_id == 8
    assert joined.context.paused is paused
    assert joined.effects == (PlaybackEffect.STOP_OUTPUT, PlaybackEffect.LOAD)


@pytest.mark.parametrize("state", list(PlaybackState))
def test_fault_keeps_entries_and_clears_attempt_and_rejoin(
    state: PlaybackState,
) -> None:
    before = _snapshot(state)
    after = decide_playback(before, _context(before), LifecycleEvent.FAULT)
    assert after.snapshot.current == before.current
    assert after.snapshot.upcoming == before.upcoming
    assert after.snapshot.state is (
        PlaybackState.ERROR if before.current else PlaybackState.IDLE
    )
    assert after.context.attempt_id is None
    assert not after.context.rejoin


@pytest.mark.parametrize("state", list(PlaybackState))
@pytest.mark.parametrize("has_next", [True, False])
def test_crossfade_reserves_next_track_without_start_or_cleanup(
    state: PlaybackState, has_next: bool
) -> None:
    before = _snapshot(state, has_next=has_next)
    if state is PlaybackState.PLAYING and has_next:
        after = decide_playback(before, _context(before), LifecycleEvent.CROSSFADE)
        assert after.snapshot.current == before.upcoming[0]
        assert after.snapshot.state is PlaybackState.LOADING
        assert after.effects == ()
        assert not after.context.history_recorded
        assert after.context.attempt_id is None
    else:
        with pytest.raises(InvalidTransitionError):
            decide_playback(before, _context(before), LifecycleEvent.CROSSFADE)
