# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import threading
from dataclasses import replace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from nahormaar_backend.application.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioStarted,
    ResolvedTrack,
    SourceResolver,
    TrackError,
    VoiceError,
    VoiceDisconnected,
    VoiceOutput,
)
from nahormaar_backend.application.crossfade import Preparation
from nahormaar_backend.application.playback import (
    CrossfadeReady,
    FadeFinished,
    PlaybackController,
    PlaybackMessage,
    PreparationFailed,
    SourceFailed,
    SourceReady,
)
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.models import PlaybackState, PlayerSnapshot, QueueEntry

if TYPE_CHECKING:
    from nahormaar_backend.application.session import SessionState


def _playback(
    *, checkpoint: PlaybackCheckpoint | None = None
) -> tuple[PlaybackController, Mock, Mock, Mock, list[PlaybackMessage], QueueEntry]:
    entry = QueueEntry("https://youtu.be/test")
    state = Mock(
        snapshot=PlayerSnapshot(state=PlaybackState.LOADING, current=entry),
        closing=False,
        faulted=False,
        change=AsyncMock(),
    )
    resolver = Mock(spec=SourceResolver)
    voice = Mock(spec=VoiceOutput, connected=True, channel_id=7, position_seconds=0)
    messages: list[PlaybackMessage] = []
    controller = PlaybackController(
        cast("SessionState", state),
        cast(SourceResolver, resolver),
        cast(VoiceOutput, voice),
        post=messages.append,
        stop_radio=Mock(),
        checkpoint=checkpoint,
    )
    return controller, state, resolver, voice, messages, entry


def test_resolver_posts_identity_and_metadata_without_mutating_playback() -> None:
    async def scenario() -> None:
        controller, state, resolver, voice, messages, entry = _playback()
        track = ResolvedTrack("stream", title="Resolved title", duration_seconds=120)
        resolver.resolve.return_value = track
        attempt = uuid4()
        controller._context = replace(
            controller._context, attempt_id=attempt, entry_id=entry.id
        )

        await controller._load(attempt, entry)

        assert messages == [SourceReady(attempt, entry.id, track)]
        state.change.assert_not_awaited()
        voice.play.assert_not_called()
        await controller.handle(messages.pop())
        state.change.assert_awaited_once()  # Metadata is ready, audio is not confirmed.
        voice.play.assert_called_once()
        assert voice.play.call_args.args[0] is track
        assert controller._resolved_track is track
        assert not controller._context.started
        await controller.handle(AudioStarted(attempt, 0))
        assert state.change.await_count == 2
        assert controller._context.started

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [TrackError("unavailable"), RuntimeError("broken")])
def test_resolver_failure_is_handled_only_when_message_is_dispatched(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        controller, state, resolver, voice, messages, entry = _playback()
        resolver.resolve.side_effect = error
        failed = AsyncMock()
        fault = AsyncMock()
        monkeypatch.setattr(controller, "_track_failed", failed)
        monkeypatch.setattr(controller, "fault", fault)
        attempt = uuid4()
        controller._context = replace(controller._context, attempt_id=attempt)

        await controller._load(attempt, entry)

        assert messages == [SourceFailed(attempt, error)]
        failed.assert_not_awaited()
        fault.assert_not_awaited()
        state.change.assert_not_awaited()
        voice.play.assert_not_called()
        await controller.handle(messages.pop())
        if isinstance(error, TrackError):
            failed.assert_awaited_once_with(error)
            fault.assert_not_awaited()
        else:
            fault.assert_awaited_once_with(
                "Source resolver failed; restart the backend."
            )
            failed.assert_not_awaited()

    asyncio.run(scenario())


def test_late_outcomes_do_not_modify_a_new_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        controller, state, _, voice, _, entry = _playback()
        old, current = uuid4(), uuid4()
        controller._context = replace(controller._context, attempt_id=current)
        key = Preparation(old, entry.id, 3, entry.source_url, 120)
        track = ResolvedTrack("old stream")
        fault = AsyncMock()
        prepare = AsyncMock()
        monkeypatch.setattr(controller, "fault", fault)
        monkeypatch.setattr(controller, "prepare_crossfade", prepare)
        for message in (
            SourceReady(old, entry.id, track),
            SourceFailed(old, RuntimeError("stale")),
            AudioCompleted(old, AudioEndReason.NATURAL, 120),
            AudioStarted(old, 0),
            CrossfadeReady(key, track),
            PreparationFailed(key, VoiceError("stale")),
            FadeFinished(old),
        ):
            await controller.handle(message)
        assert controller.attempt_id == current
        state.change.assert_not_awaited()
        voice.play.assert_not_called()
        voice.stop.assert_not_awaited()
        fault.assert_not_awaited()
        prepare.assert_not_awaited()

    asyncio.run(scenario())


def test_audio_thread_only_posts_messages_on_the_session_loop() -> None:
    async def scenario() -> None:
        controller, state, _, voice, messages, _ = _playback()
        attempt = uuid4()
        posting_threads: list[int] = []
        loop_thread = threading.get_ident()

        def post(message: PlaybackMessage) -> None:
            posting_threads.append(threading.get_ident())
            messages.append(message)

        controller._post = post
        started = AudioStarted(attempt, 12)
        completed = AudioCompleted(attempt, AudioEndReason.INTERRUPTED, 45)
        disconnected = VoiceDisconnected(attempt, 7, 45, False)
        faded = FadeFinished(attempt)
        await asyncio.to_thread(controller._post_from_thread, started)
        await asyncio.to_thread(controller._post_from_thread, completed)
        await asyncio.to_thread(controller._post_from_thread, disconnected)
        await asyncio.to_thread(controller._post_from_thread, faded)
        await asyncio.sleep(0)

        assert messages == [started, completed, disconnected, faded]
        assert posting_threads == [loop_thread] * 4
        state.change.assert_not_awaited()
        voice.stop.assert_not_awaited()

    asyncio.run(scenario())


def test_closed_playback_does_not_post_late_audio_callbacks() -> None:
    async def scenario() -> None:
        controller, state, _, _, messages, entry = _playback()
        attempt = uuid4()
        key = Preparation(attempt, entry.id, 3, entry.source_url, 120)
        state.closing = True
        controller._post_from_thread(AudioStarted(attempt, 0))
        controller._post_from_thread(
            AudioCompleted(attempt, AudioEndReason.NATURAL, 120)
        )
        controller._post_from_thread(VoiceDisconnected(attempt, 7, 120, False))
        controller._crossfade_due(key, ResolvedTrack("stream"))
        controller._post_from_thread(FadeFinished(attempt))
        controller._preparation_failed(key, VoiceError("late"))
        await asyncio.sleep(0)
        assert messages == []

    asyncio.run(scenario())


def test_fade_completion_recomputes_preparation_without_a_state_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        controller, state, _, _, _, _ = _playback()
        attempt = uuid4()
        controller._context = replace(controller._context, attempt_id=attempt)
        prepare = AsyncMock()
        monkeypatch.setattr(controller, "prepare_crossfade", prepare)
        await controller.handle(FadeFinished(attempt))
        prepare.assert_awaited_once_with()
        state.change.assert_not_awaited()

    asyncio.run(scenario())


def _prepared_playback() -> tuple[PlaybackController, Mock, Mock, Preparation]:
    controller, state, _, voice, _, current = _playback()
    first = QueueEntry("https://youtu.be/next")
    second = QueueEntry("https://youtu.be/later")
    state.snapshot = PlayerSnapshot(
        state=PlaybackState.PLAYING,
        current=current,
        upcoming=(first, second),
        crossfade_seconds=3,
    )
    voice.transitioning = False
    controller._context = replace(
        controller._context,
        attempt_id=uuid4(),
        entry_id=current.id,
        started=True,
        history_recorded=True,
        channel_id=7,
        rejoin=True,
    )
    controller._resolved_track = ResolvedTrack("current", duration_seconds=120)
    key = controller._eligible_preparation()
    assert key is not None
    controller._crossfade.key = key
    return controller, state, voice, key


@pytest.mark.parametrize(
    "change", ["disabled", "duration", "removed", "reordered", "source", "loading"]
)
def test_queued_crossfade_outcomes_recheck_state_before_prepare_subscription(
    change: str,
) -> None:
    async def scenario() -> None:
        controller, state, voice, key = _prepared_playback()
        snapshot = cast(PlayerSnapshot, state.snapshot)
        if change == "disabled":
            updated = replace(snapshot, crossfade_seconds=0)
        elif change == "duration":
            updated = replace(snapshot, crossfade_seconds=5)
        elif change == "removed":
            updated = replace(snapshot, upcoming=())
        elif change == "reordered":
            updated = replace(snapshot, upcoming=tuple(reversed(snapshot.upcoming)))
        elif change == "source":
            updated = replace(
                snapshot,
                upcoming=(
                    replace(
                        snapshot.upcoming[0], source_url="https://youtu.be/changed"
                    ),
                    snapshot.upcoming[1],
                ),
            )
        else:
            updated = replace(snapshot, state=PlaybackState.LOADING)
        state.snapshot = updated

        # The pending PREPARE message has not invalidated the cached key yet.
        assert controller._crossfade.key == key
        await controller.handle(CrossfadeReady(key, ResolvedTrack("prepared")))
        await controller.handle(PreparationFailed(key, VoiceError("late failure")))

        assert state.snapshot is updated
        assert controller.attempt_id == key.attempt_id
        assert controller._crossfade.key == key
        state.change.assert_not_awaited()
        voice.start_transition.assert_not_called()
        voice.play.assert_not_called()
        voice.stop.assert_not_awaited()

    asyncio.run(scenario())


def test_current_crossfade_defers_while_paused_and_logs_speculative_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        controller, state, voice, key = _prepared_playback()
        state.snapshot = replace(
            cast(PlayerSnapshot, state.snapshot), state=PlaybackState.PAUSED
        )
        track = ResolvedTrack("prepared")
        await controller.handle(CrossfadeReady(key, track))
        assert controller._deferred_fade == (key, track)
        state.change.assert_not_awaited()
        voice.start_transition.assert_not_called()
        await controller.handle(
            PreparationFailed(key, VoiceError("current preparation failed"))
        )
        assert "crossfade.preparation_failed" in caplog.text
        state.change.assert_not_awaited()
        voice.stop.assert_not_awaited()

    asyncio.run(scenario())


def test_current_crossfade_suspends_when_voice_is_disconnected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        controller, state, voice, key = _prepared_playback()
        voice.connected = False
        suspend = AsyncMock()
        monkeypatch.setattr(controller, "_suspend", suspend)
        await controller.handle(CrossfadeReady(key, ResolvedTrack("prepared")))
        suspend.assert_awaited_once_with(explicit=False)
        state.change.assert_not_awaited()
        voice.start_transition.assert_not_called()

    asyncio.run(scenario())


def test_restore_runs_in_the_handler_and_leaves_error_policy_to_session() -> None:
    async def scenario() -> None:
        checkpoint = PlaybackCheckpoint(7, None, 0, False, 0.5)
        controller, state, _, voice, messages, _ = _playback(checkpoint=checkpoint)
        voice.connected = False
        voice.connect.side_effect = VoiceError("channel unavailable")

        with pytest.raises(VoiceError, match="channel unavailable"):
            await controller.restore()
        assert messages == []
        voice.connect.assert_awaited_once_with(7)
        voice.set_volume.assert_called_once_with(0.5)
        state.change.assert_awaited_once()

    asyncio.run(scenario())


def test_cancelled_resolver_cleanup_failure_is_not_posted_as_a_new_outcome() -> None:
    async def scenario() -> None:
        controller, _, resolver, _, messages, entry = _playback()
        entered = asyncio.Event()

        async def failing_cleanup(source_url: str) -> ResolvedTrack:
            del source_url
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                raise RuntimeError("cleanup failed") from error
            raise AssertionError("unreachable")

        resolver.resolve.side_effect = failing_cleanup
        task = asyncio.create_task(controller._load(uuid4(), entry))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(VoiceError, match="Source extraction cleanup failed"):
            await task
        assert messages == []

    asyncio.run(scenario())
