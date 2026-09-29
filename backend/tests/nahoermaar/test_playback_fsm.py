# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.player.domain import (
    ListeningSessionId,
    OperationId,
    PlaybackIntent,
    PlayerAction,
    PlayerError,
    PlayerErrorCode,
    PlayerState,
    SuspensionReason,
)
from nahoermaar.player.events import (
    AddTracks,
    FailPlayback,
    JoinVoice,
    LeaveVoice,
    Play,
    SetPlayerSettings,
    SetSleepTimer,
    StopPlayback,
    SuspendPlayback,
    TrackSelection,
)
from nahoermaar.player.fsm import transition
from nahoermaar.users.domain import UserId

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def _operation() -> OperationId:
    return OperationId(uuid4())


def _queued_state(count: int = 2) -> tuple[PlayerState, UserId]:
    session_id = ListeningSessionId(uuid4())
    actor_id = UserId(uuid4())
    result = transition(
        PlayerState.empty(session_id, NOW),
        AddTracks(
            session_id,
            _operation(),
            tuple(
                TrackSelection(TrackId(uuid4()), TrackSourceId(uuid4()))
                for _ in range(count)
            ),
        ),
        actor_id,
        NOW,
    )
    return result.state, actor_id


def test_stop_returns_the_current_request_to_the_front() -> None:
    queued, actor_id = _queued_state()
    current = queued.queue.entries[0].request
    joined = transition(
        queued,
        JoinVoice(queued.session.id, _operation(), 123456789),
        actor_id,
        NOW,
    )
    playing = transition(
        joined.state,
        Play(joined.state.session.id, _operation()),
        actor_id,
        NOW,
    )

    stopped = transition(
        playing.state,
        StopPlayback(playing.state.session.id, _operation()),
        actor_id,
        NOW,
    )

    assert stopped.outcome.action is PlayerAction.PLAYBACK_STOPPED
    assert stopped.state.checkpoint.intent is PlaybackIntent.STOPPED
    assert stopped.state.queue.entries[0].request == current
    assert stopped.outcome.restored_count == 1


def test_failed_source_returns_after_the_remaining_queue() -> None:
    queued, actor_id = _queued_state()
    failed_request = queued.queue.entries[0].request
    remaining_request = queued.queue.entries[1].request
    joined = transition(
        queued,
        JoinVoice(queued.session.id, _operation(), 123456789),
        actor_id,
        NOW,
    )
    playing = transition(
        joined.state,
        Play(joined.state.session.id, _operation()),
        actor_id,
        NOW,
    )

    failed = transition(
        playing.state,
        FailPlayback(
            playing.state.session.id,
            _operation(),
            failed_request.id,
        ),
        None,
        NOW,
    )

    assert failed.outcome.action is PlayerAction.PLAYBACK_FAILED
    assert failed.state.checkpoint.intent is PlaybackIntent.STOPPED
    assert tuple(entry.request for entry in failed.state.queue.entries) == (
        remaining_request,
        failed_request,
    )
    assert failed.outcome.restored_count == 1


def test_play_requires_a_voice_target_before_mutating_state() -> None:
    queued, actor_id = _queued_state()

    with pytest.raises(PlayerError) as captured:
        transition(
            queued,
            Play(queued.session.id, _operation()),
            actor_id,
            NOW,
        )

    assert captured.value.code is PlayerErrorCode.VOICE_CHANNEL_REQUIRED
    assert captured.value.status == 409
    assert queued.checkpoint.intent is PlaybackIntent.STOPPED
    assert len(queued.queue.entries) == 2


def test_voice_target_is_persisted_by_the_same_fsm() -> None:
    state, actor_id = _queued_state(1)
    joined = transition(
        state,
        JoinVoice(state.session.id, _operation(), 123456789),
        actor_id,
        NOW,
    )
    left = transition(
        joined.state,
        LeaveVoice(joined.state.session.id, _operation()),
        actor_id,
        NOW,
    )

    assert joined.state.session.channel_id == 123456789
    assert joined.outcome.action is PlayerAction.VOICE_JOINED
    assert left.state.session.channel_id is None
    assert left.outcome.action is PlayerAction.VOICE_LEFT


def test_leaving_voice_pauses_and_retains_the_current_request() -> None:
    queued, actor_id = _queued_state()
    joined = transition(
        queued,
        JoinVoice(queued.session.id, _operation(), 123456789),
        actor_id,
        NOW,
    )
    playing = transition(
        joined.state,
        Play(joined.state.session.id, _operation()),
        actor_id,
        NOW,
    )

    left = transition(
        playing.state,
        LeaveVoice(playing.state.session.id, _operation()),
        actor_id,
        NOW,
    )

    assert left.outcome.action is PlayerAction.VOICE_LEFT
    assert left.state.session.channel_id is None
    assert left.state.checkpoint.intent is PlaybackIntent.PAUSED
    assert left.state.checkpoint.request == playing.state.checkpoint.request
    assert left.state.checkpoint.position_seconds == 0
    assert left.state.queue == playing.state.queue


def test_player_settings_are_updated_atomically() -> None:
    state, actor_id = _queued_state(1)

    updated = transition(
        state,
        SetPlayerSettings(
            state.session.id,
            _operation(),
            volume=0.35,
            crossfade_seconds=6,
        ),
        actor_id,
        NOW,
    )

    assert updated.state.session.volume == 0.35
    assert updated.state.session.crossfade_seconds == 6
    assert updated.outcome.action is PlayerAction.SETTINGS_UPDATED


def test_unattended_suspend_preserves_queue_checkpoint_and_sleep_timer() -> None:
    queued, actor_id = _queued_state()
    joined = transition(
        queued,
        JoinVoice(queued.session.id, _operation(), 123456789),
        actor_id,
        NOW,
    )
    playing = transition(
        joined.state,
        Play(joined.state.session.id, _operation()),
        actor_id,
        NOW,
    )
    sleep_at = NOW + timedelta(hours=1)
    armed = transition(
        playing.state,
        SetSleepTimer(playing.state.session.id, _operation(), sleep_at),
        actor_id,
        NOW,
    )

    suspended = transition(
        armed.state,
        SuspendPlayback(
            armed.state.session.id,
            _operation(),
            SuspensionReason.EMPTY_AUDIENCE,
        ),
        None,
        NOW,
    )

    assert suspended.outcome.action is PlayerAction.PLAYBACK_SUSPENDED
    assert suspended.state.session.channel_id is None
    assert suspended.state.session.sleep_at == sleep_at
    assert suspended.state.checkpoint.request == armed.state.checkpoint.request
    assert suspended.state.checkpoint.position_seconds == 0
    assert suspended.state.checkpoint.intent is PlaybackIntent.PAUSED
    assert suspended.state.queue == armed.state.queue


def test_sleep_timer_suspend_clears_only_its_own_deadline() -> None:
    queued, actor_id = _queued_state(1)
    armed = transition(
        queued,
        SetSleepTimer(
            queued.session.id,
            _operation(),
            NOW + timedelta(minutes=30),
        ),
        actor_id,
        NOW,
    )

    suspended = transition(
        armed.state,
        SuspendPlayback(
            armed.state.session.id,
            _operation(),
            SuspensionReason.SLEEP_TIMER,
        ),
        None,
        NOW,
    )

    assert suspended.state.session.sleep_at is None
    assert suspended.state.queue == armed.state.queue
