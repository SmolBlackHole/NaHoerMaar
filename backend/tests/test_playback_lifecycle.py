# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Lifecycle acceptance with isolated storage and manually confirmed media frames."""

# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4
from typing import Literal
from collections.abc import Callable

import pytest

from nahormaar_backend.application.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioStarted,
    ResolvedTrack,
    TrackError,
    VoiceError,
)
from nahormaar_backend.application.events import SessionEvent, TrackStarted
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.commands import Control, Seek
from nahormaar_backend.domain.fsm import (
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
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_playback import FakeVoice, _controller, _wait_until
from test_crossfade import _playing


def test_fsm_retry_uses_consumed_position_once_and_rejects_stale_completions() -> None:
    first, second = QueueEntry("first"), QueueEntry("second")
    attempt = uuid4()
    snapshot = PlayerSnapshot(
        state=PlaybackState.PLAYING, current=first, upcoming=(second,)
    )
    context = PlaybackContext(first.id, attempt, started=True, history_recorded=True)
    retry = decide_playback(
        snapshot,
        context,
        LifecycleEvent.INTERRUPTED,
        attempt_id=attempt,
        position_seconds=45,
    )
    assert retry.snapshot.current == first
    assert retry.context.position_seconds == 45
    assert retry.context.retries == 1
    assert retry.context.history_recorded
    assert PlaybackEffect.LOAD in retry.effects
    assert not retry.context.started
    retry_attempt = uuid4()
    retry_context = replace(retry.context, attempt_id=retry_attempt)
    stale = decide_playback(
        retry.snapshot,
        retry_context,
        LifecycleEvent.FINISHED,
        attempt_id=attempt,
        position_seconds=111,
    )
    assert stale.context == retry_context and not stale.effects
    exhausted = decide_playback(
        retry.snapshot,
        retry_context,
        LifecycleEvent.INTERRUPTED,
        attempt_id=retry_attempt,
        position_seconds=48,
    )
    assert exhausted.snapshot.current == second
    assert exhausted.context.retries == 0
    assert exhausted.context.position_seconds == 0
    assert PlaybackEffect.REPORT_FAILURE in exhausted.effects


def test_frame_confirmation_counts_once_and_keeps_a_newer_pause() -> None:
    entry, attempt = QueueEntry("first"), uuid4()
    snapshot = PlayerSnapshot(state=PlaybackState.PAUSED, current=entry)
    context = PlaybackContext(entry.id, attempt, paused=True)
    started = decide_playback(
        snapshot,
        context,
        LifecycleEvent.STARTED,
        attempt_id=attempt,
        position_seconds=0.02,
    )
    assert started.snapshot.state is PlaybackState.PAUSED
    assert started.effects == (PlaybackEffect.RECORD_HISTORY,)
    duplicate = decide_playback(
        started.snapshot,
        started.context,
        LifecycleEvent.STARTED,
        attempt_id=attempt,
        position_seconds=0.02,
    )
    assert duplicate.effects == ()
    assert duplicate.context == started.context


def test_confirmed_events_follow_history_commit_and_duplicates_do_not_count(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        voice = FakeVoice()
        voice.auto_start = False
        session, resolver, _, database = await _controller(tmp_path, voice=voice)
        observed: list[TrackStarted] = []

        async def observe(event: SessionEvent) -> None:
            assert isinstance(event, TrackStarted)
            with SQLiteStore(database) as store:
                assert event.play in store.load().recently_played
            observed.append(event)

        subscription = session.events.subscribe((TrackStarted,), observe)
        try:
            await session.enqueue(QueueEntry("first"))
            await session.connect(7)
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(lambda: len(voice.played) == 1)
            assert not session.snapshot.recently_played
            assert not observed
            voice.confirm_start()
            voice.confirm_start()
            await _wait_until(lambda: len(observed) == 1)
            await session.read_status()
            assert len(session.snapshot.recently_played) == 1
            assert not subscription.closed
        finally:
            await session.close()

    asyncio.run(scenario())


def test_interruption_at_45_of_111_survives_cleanup_and_retries_once(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session, resolver, voice, database = await _controller(tmp_path)
        first, second = QueueEntry("first"), QueueEntry("second")
        try:
            await session.enqueue(first)
            await session.enqueue(second)
            await session.connect(7)
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.requests[0].set_result(
                ResolvedTrack("first-stream", duration_seconds=111)
            )
            await _wait_until(lambda: session.snapshot.state is PlaybackState.PLAYING)
            first_attempt = voice.attempts[0]
            voice.position_seconds = (
                None  # The real adapter has already cleaned its source.
            )
            interrupted = AudioCompleted(
                first_attempt,
                AudioEndReason.INTERRUPTED,
                45,
                TrackError("Stream interrupted", retryable=True),
            )
            voice.notifications[0](interrupted)
            voice.notifications[0](interrupted)
            await _wait_until(lambda: len(resolver.requests) == 2)
            with SQLiteStore(database) as store:
                assert store.checkpoint() == PlaybackCheckpoint(
                    7, first.id, 45, history_recorded=True
                )
            resolver.requests[1].set_result(
                ResolvedTrack("fresh-stream", duration_seconds=111)
            )
            await _wait_until(lambda: session.snapshot.state is PlaybackState.PLAYING)
            assert voice.positions == [0, 45]
            assert resolver.calls == ["first", "first"]
            assert len(session.snapshot.recently_played) == 1
            voice.notifications[1](
                AudioCompleted(
                    voice.attempts[1],
                    AudioEndReason.INTERRUPTED,
                    48,
                    TrackError("Again", retryable=True),
                )
            )
            await _wait_until(lambda: len(resolver.requests) == 3)
            assert resolver.calls == ["first", "first", "second"]
            assert session.snapshot.current == second
            assert (
                session.status.last_issue
                and session.status.last_issue.entry_id == first.id
            )
        finally:
            await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_join_resumes_retained_position_without_double_history(
    tmp_path: Path, paused: bool, explicit: bool
) -> None:
    async def scenario() -> None:
        session, resolver, voice, database = await _controller(tmp_path)
        entry = QueueEntry("first")
        try:
            await session.enqueue(entry)
            await session.connect(7)
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(lambda: session.snapshot.state is PlaybackState.PLAYING)
            voice.position_seconds = 45
            if paused:
                await session.pause()
            if explicit:
                await session.disconnect()
            else:
                voice.lose_connection()
                await _wait_until(
                    lambda: session.snapshot.voice_state is VoiceState.DISCONNECTED
                )
            with SQLiteStore(database) as store:
                assert store.checkpoint() == (
                    None
                    if explicit
                    else PlaybackCheckpoint(
                        7, entry.id, 45, paused, history_recorded=True
                    )
                )
            await session.connect(7)
            await _wait_until(lambda: len(resolver.requests) == 2)
            resolver.succeed(1)
            await _wait_until(lambda: len(voice.positions) == 2)
            assert voice.positions == [0, 45]
            assert voice.paused is paused
            assert len(session.snapshot.recently_played) == 1
            await session.connect(7)
            assert len(voice.played) == 2
        finally:
            await session.close()

    asyncio.run(scenario())


def test_seek_pause_and_stale_start_do_not_create_false_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        voice = FakeVoice()
        voice.auto_start = False
        session, resolver, _, _ = await _controller(tmp_path, voice=voice)
        try:
            await session.enqueue(QueueEntry("first"))
            await session.connect(7)
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.requests[0].set_result(
                ResolvedTrack("stream", duration_seconds=111)
            )
            await _wait_until(lambda: len(voice.played) == 1)
            await session.pause()
            await session.request(uuid4(), Seek(45, voice.attempts[-1]))
            assert voice.paused
            old = AudioStarted(voice.attempts[0], 0.02)
            voice.notifications[0](old)
            await session.read_status()
            assert not session.snapshot.recently_played
            await session.play()
            voice.confirm_start()
            await _wait_until(lambda: len(session.snapshot.recently_played) == 1)
            await session.request(uuid4(), Seek(60, voice.attempts[-1]))
            voice.confirm_start()
            await session.read_status()
            await session.stop()
            voice.notifications[-1](AudioStarted(voice.attempts[-1], 60.02))
            await session.read_status()
            assert session.snapshot.state is PlaybackState.IDLE
            assert len(session.snapshot.recently_played) == 1
        finally:
            await session.close()

    asyncio.run(scenario())


def test_failed_crossfade_activation_and_fallback_never_records_incoming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, resolver, voice, _, entries = await _playing(tmp_path)
        try:

            def fail(*args: object, **kwargs: object) -> bool:
                raise VoiceError("Output unavailable")

            monkeypatch.setattr(voice, "start_transition", fail)
            monkeypatch.setattr(voice, "play", fail)
            voice.fade_due()
            await _wait_until(lambda: session.snapshot.current == entries[2])
            assert len(session.snapshot.recently_played) == 1
            assert (
                session.status.last_issue
                and session.status.last_issue.entry_id == entries[1].id
            )
            assert resolver.calls[:2] == [entry.source_url for entry in entries[:2]]
        finally:
            await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["pause", "stop"])
def test_direct_control_cannot_mutate_successor_after_completion(
    tmp_path: Path, action: Literal["pause", "stop"]
) -> None:
    async def scenario() -> None:
        session, resolver, voice, _ = await _controller(tmp_path)
        first, second = QueueEntry("first"), QueueEntry("second")
        try:
            await session.enqueue(first)
            await session.enqueue(second)
            await session.connect(7)
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(lambda: session.snapshot.state is PlaybackState.PLAYING)
            attempt = voice.attempts[0]
            session._post(AudioCompleted(attempt, AudioEndReason.NATURAL, 111))
            control = session._accept(Control(action, attempt))
            await control
            assert session.snapshot.current == second
            assert session.snapshot.state is PlaybackState.LOADING
            assert voice.pause_count == 0
        finally:
            await session.close()

    asyncio.run(scenario())


def test_restart_unconfirmed_replay_is_distinct_from_earlier_play(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session, resolver, voice, database = await _controller(tmp_path)
        entry = QueueEntry("first")
        try:
            await session.enqueue(entry)
            await session.connect(7)
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(lambda: len(session.snapshot.recently_played) == 1)
            await session.stop()
            voice.auto_start = False
            await session.play()
            await _wait_until(lambda: len(resolver.requests) == 2)
            resolver.succeed(1)
            await _wait_until(lambda: len(voice.played) == 2)
        finally:
            await session.close()
        with SQLiteStore(database) as store:
            checkpoint = store.checkpoint()
            assert checkpoint is not None and not checkpoint.history_recorded
            assert len(store.load().recently_played) == 1
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            await restored.restore()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(lambda: restored.snapshot.state is PlaybackState.PLAYING)
            assert len(restored.snapshot.recently_played) == 2
            assert all(
                item.entry.id == entry.id for item in restored.snapshot.recently_played
            )
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_partial_activation_stop_cannot_complete_fallback_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        session, _, voice, _, entries = await _playing(tmp_path)
        original_stop = voice.stop
        try:

            def partial(
                track: ResolvedTrack,
                attempt: UUID,
                notify: Callable[[AudioEvent], None],
                on_faded: Callable[[], None],
            ) -> bool:
                voice.auto_start = False
                voice.play(track, attempt, notify)
                voice.auto_start = True
                raise VoiceError("Activation failed after binding")

            async def stop_partial() -> None:
                attempt, notify = voice.attempts[-1], voice.notifications[-1]
                await original_stop()
                notify(AudioCompleted(attempt, AudioEndReason.STOPPED, 0))

            monkeypatch.setattr(voice, "start_transition", partial)
            monkeypatch.setattr(voice, "stop", stop_partial)
            voice.fade_due()
            await _wait_until(lambda: len(session.snapshot.recently_played) == 2)
            assert session.snapshot.current is not None
            assert session.snapshot.current.id == entries[1].id
            assert session.snapshot.state is PlaybackState.PLAYING
            assert len(voice.played) == 3
            assert len(set(voice.attempts)) == 3
            assert [item.entry.id for item in session.snapshot.recently_played] == [
                entries[1].id,
                entries[0].id,
            ]
        finally:
            await session.close()

    asyncio.run(scenario())
