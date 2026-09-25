# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, datetime
from uuid import uuid4

from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.player.domain import (
    ListeningSessionId,
    OperationId,
    PlaybackIntent,
    PlayerAction,
    PlayerState,
)
from nahoermaar.player.events import (
    AddTracks,
    FailPlayback,
    JoinVoice,
    LeaveVoice,
    Play,
    StopPlayback,
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
    playing = transition(
        queued,
        Play(queued.session.id, _operation()),
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
    playing = transition(
        queued,
        Play(queued.session.id, _operation()),
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
