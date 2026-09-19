# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from nahormaar_backend.fsm import (
    InvalidTransitionError,
    PlaybackEvent,
    VoiceEvent,
    transition,
    voice_transition,
)
from nahormaar_backend.models import (
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


def test_start_pause_and_resume_keep_the_same_entry() -> None:
    idle = _snapshot(PlaybackState.IDLE)
    loading = transition(idle, PlaybackEvent.PLAY)
    assert loading.state is PlaybackState.LOADING
    assert loading.current == idle.upcoming[0]
    assert loading.upcoming == ()
    playing = transition(loading, PlaybackEvent.READY)
    paused = transition(playing, PlaybackEvent.PAUSE)
    assert paused.state is PlaybackState.PAUSED
    assert paused.current == playing.current
    assert transition(paused, PlaybackEvent.PLAY) == playing
    assert idle.current is None


@pytest.mark.parametrize("state", list(PlaybackState))
def test_recovery_requeues_once_and_resets_connection(state: PlaybackState) -> None:
    before = _snapshot(state)
    after = transition(before, PlaybackEvent.RECOVER)
    expected = ((before.current,) if before.current else ()) + before.upcoming
    assert after == PlayerSnapshot(upcoming=expected)
    assert transition(after, PlaybackEvent.RECOVER) == after


@pytest.mark.parametrize("state", list(PlaybackState))
def test_stop_keeps_all_entries_and_connection(state: PlaybackState) -> None:
    before = _snapshot(state)
    after = transition(before, PlaybackEvent.STOP)
    assert after.state is PlaybackState.IDLE
    assert after.current is None
    assert after.upcoming == (
        ((before.current,) if before.current else ()) + before.upcoming
    )
    assert after.voice_state is VoiceState.CONNECTED


@pytest.mark.parametrize("has_next", [True, False])
@pytest.mark.parametrize("state", list(PlaybackState))
def test_skip_advances_only_an_active_player(
    state: PlaybackState, has_next: bool
) -> None:
    before = _snapshot(state, has_next=has_next)
    after = transition(before, PlaybackEvent.SKIP)
    if state is PlaybackState.IDLE:
        assert after == before
    elif has_next:
        assert after.current == before.upcoming[0]
        assert after.state is PlaybackState.LOADING
        assert after.upcoming == ()
    else:
        assert after.current is None
        assert after.state is PlaybackState.IDLE


def test_play_with_an_empty_queue_stays_idle() -> None:
    empty = PlayerSnapshot()
    assert transition(empty, PlaybackEvent.PLAY) == empty


@pytest.mark.parametrize(
    "state", [PlaybackState.LOADING, PlaybackState.PLAYING, PlaybackState.PAUSED]
)
def test_failure_and_retry_keep_the_failed_entry(state: PlaybackState) -> None:
    before = _snapshot(state)
    failed = transition(before, PlaybackEvent.FAIL)
    assert failed.state is PlaybackState.ERROR
    assert failed.current == before.current
    retry = transition(failed, PlaybackEvent.PLAY)
    assert retry.state is PlaybackState.LOADING
    assert retry.current == before.current
    assert retry.upcoming == before.upcoming


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (PlaybackState.IDLE, PlaybackEvent.READY),
        (PlaybackState.IDLE, PlaybackEvent.PAUSE),
        (PlaybackState.IDLE, PlaybackEvent.FAIL),
        (PlaybackState.LOADING, PlaybackEvent.PLAY),
        (PlaybackState.LOADING, PlaybackEvent.PAUSE),
        (PlaybackState.PLAYING, PlaybackEvent.PLAY),
        (PlaybackState.PLAYING, PlaybackEvent.READY),
        (PlaybackState.PAUSED, PlaybackEvent.READY),
        (PlaybackState.PAUSED, PlaybackEvent.PAUSE),
        (PlaybackState.ERROR, PlaybackEvent.READY),
        (PlaybackState.ERROR, PlaybackEvent.PAUSE),
        (PlaybackState.ERROR, PlaybackEvent.FAIL),
    ],
)
def test_invalid_events_do_not_change_state(
    state: PlaybackState, event: PlaybackEvent
) -> None:
    snapshot = _snapshot(state)
    with pytest.raises(InvalidTransitionError) as error:
        transition(snapshot, event)
    assert error.value.state is state
    assert error.value.event is event
    assert snapshot.state is state


@pytest.mark.parametrize("state", [PlaybackState.PLAYING, PlaybackState.PAUSED])
@pytest.mark.parametrize("has_next", [True, False])
def test_natural_completion_advances_even_if_pause_raced_with_end(
    state: PlaybackState, has_next: bool
) -> None:
    snapshot = _snapshot(state, has_next=has_next)
    after = transition(snapshot, PlaybackEvent.FINISHED)
    assert after.current == (snapshot.upcoming[0] if has_next else None)
    assert after.state is (PlaybackState.LOADING if has_next else PlaybackState.IDLE)


@pytest.mark.parametrize(
    "state", [PlaybackState.IDLE, PlaybackState.LOADING, PlaybackState.ERROR]
)
def test_completion_requires_a_started_track(state: PlaybackState) -> None:
    with pytest.raises(InvalidTransitionError):
        transition(_snapshot(state), PlaybackEvent.FINISHED)


def test_voice_connect_events_require_a_pending_connection() -> None:
    disconnected = PlayerSnapshot()
    with pytest.raises(ValueError):
        voice_transition(disconnected, VoiceEvent.CONNECTED)
    connecting = voice_transition(disconnected, VoiceEvent.CONNECT)
    assert connecting.voice_state is VoiceState.CONNECTING
    with pytest.raises(ValueError):
        voice_transition(connecting, VoiceEvent.CONNECT)
    connected = voice_transition(connecting, VoiceEvent.CONNECTED)
    assert connected.voice_state is VoiceState.CONNECTED
    assert voice_transition(connecting, VoiceEvent.DISCONNECT) == disconnected
    assert voice_transition(connected, VoiceEvent.DISCONNECT) == disconnected


def test_voice_loss_preserves_track_and_does_not_resume_on_reconnect() -> None:
    playing = _snapshot(PlaybackState.PLAYING)
    disconnected = voice_transition(playing, VoiceEvent.DISCONNECT)
    assert disconnected.current is None
    assert disconnected.upcoming == (playing.current, *playing.upcoming)
    assert voice_transition(disconnected, VoiceEvent.DISCONNECT) == disconnected
    connecting = voice_transition(disconnected, VoiceEvent.CONNECT)
    reconnected = voice_transition(connecting, VoiceEvent.CONNECTED)
    assert reconnected.state is PlaybackState.IDLE
    assert reconnected.upcoming == disconnected.upcoming
