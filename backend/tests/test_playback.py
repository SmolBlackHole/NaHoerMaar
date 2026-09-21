# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from uuid import UUID

import pytest

from nahormaar_backend.api.schemas import State
from nahormaar_backend.application.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioStarted,
    ResolvedTrack,
    TrackError,
    VoiceChannelInfo,
    VoiceDisconnected,
)
from nahormaar_backend.application.session import Session
from nahormaar_backend.domain.models import (
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    VoiceState,
)
from nahormaar_backend.persistence.player_store import SQLiteStore


class ControlledResolver:
    def __init__(self, *, cancelled_result: ResolvedTrack | None = None) -> None:
        self.calls: list[str] = []
        self.requests: list[asyncio.Future[ResolvedTrack]] = []
        self.cancelled: list[str] = []
        self.cancelled_result = cancelled_result

    async def resolve(self, source_url: str) -> ResolvedTrack:
        self.calls.append(source_url)
        request = asyncio.get_running_loop().create_future()
        self.requests.append(request)
        try:
            return await asyncio.shield(request)
        except asyncio.CancelledError:
            self.cancelled.append(source_url)
            if self.cancelled_result is not None:
                return self.cancelled_result
            raise

    def succeed(self, index: int, name: str = "audio") -> None:
        self.requests[index].set_result(ResolvedTrack(f"https://stream.invalid/{name}"))

    def fail(self, index: int, message: str, *, retryable: bool = False) -> None:
        self.requests[index].set_exception(TrackError(message, retryable=retryable))


class FakeVoice:
    def __init__(self) -> None:
        self._connected = False
        self._channel_id: int | None = None
        self._disconnect_handler: Callable[[VoiceDisconnected], None] = lambda event: (
            None
        )
        self.played: list[ResolvedTrack] = []
        self.positions: list[float] = []
        self.callbacks: list[Callable[[Exception | None], None]] = []
        self.attempts: list[UUID] = []
        self.notifications: list[Callable[[AudioEvent], None]] = []
        self.auto_start = True
        self.paused = False
        self.media_started = False
        self.stop_count = 0
        self.pause_count = 0
        self.resume_count = 0
        self.volumes: list[float] = []
        self.disconnect_count = 0
        self.transitioning = False
        self.prepared: ResolvedTrack | None = None
        self.fade_due: Callable[[], None] = lambda: None
        self.fade_finished: Callable[[], None] = lambda: None
        self.fade_seconds = 0.0
        self.position_seconds: float | None = None

    async def prepare_next(
        self, track: ResolvedTrack, seconds: float, on_due: Callable[[], None]
    ) -> bool:
        self.prepared = track
        self.fade_seconds = seconds
        self.fade_due = on_due
        return True

    async def discard_next(self) -> None:
        self.prepared = None

    def start_transition(
        self,
        track: ResolvedTrack,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        on_faded: Callable[[], None],
    ) -> bool:
        if self.prepared is None:
            return False
        self.prepared = None
        self.transitioning = True
        self.fade_finished = on_faded
        self.play(track, attempt_id, notify)
        return True

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def channel_id(self) -> int | None:
        return self._channel_id

    def channels(self) -> tuple[VoiceChannelInfo, ...]:
        return (VoiceChannelInfo(7, "Music", True, True, 1, "Test server"),)

    def set_disconnect_handler(
        self, handler: Callable[[VoiceDisconnected], None]
    ) -> None:
        self._disconnect_handler = handler

    async def connect(self, channel_id: int) -> None:
        self._connected = True
        self._channel_id = channel_id

    async def disconnect(self) -> None:
        self.disconnect_count += 1
        self._connected = False
        self._channel_id = None

    async def close(self) -> None:
        await self.disconnect()

    def play(
        self,
        track: ResolvedTrack,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None:
        self.played.append(track)
        self.positions.append(position_seconds)
        self.position_seconds = position_seconds
        self.attempts.append(attempt_id)
        self.notifications.append(notify)

        def completed(error: Exception | None) -> None:
            reason = (
                AudioEndReason.NATURAL
                if error is None
                else (
                    AudioEndReason.INTERRUPTED
                    if isinstance(error, TrackError) and error.retryable
                    else AudioEndReason.OUTPUT_FAILED
                )
            )
            notify(
                AudioCompleted(attempt_id, reason, self.position_seconds or 0, error)
            )

        self.callbacks.append(completed)
        self.media_started = False
        self.paused = paused
        if paused:
            self.pause()
        elif self.auto_start:
            self.confirm_start()

    def confirm_start(self) -> None:
        self.media_started = True
        self.notifications[-1](
            AudioStarted(self.attempts[-1], self.position_seconds or 0)
        )

    async def stop(self) -> None:
        self.stop_count += 1
        self.position_seconds = None
        self.transitioning = False
        self.prepared = None

    def pause(self) -> None:
        self.pause_count += 1
        self.paused = True

    def resume(self) -> None:
        self.resume_count += 1
        self.paused = False
        if not self.media_started and self.auto_start:
            self.confirm_start()

    def set_volume(self, volume: float) -> None:
        self.volumes.append(volume)

    def complete(self, index: int, error: Exception | None = None) -> None:
        self.callbacks[index](error)

    def lose_connection(self) -> None:
        event = VoiceDisconnected(
            self.attempts[-1] if self.attempts else None,
            self._channel_id,
            self.position_seconds or 0,
            self.paused,
        )
        self._connected = False
        self._channel_id = None
        self.position_seconds = None
        self._disconnect_handler(event)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def _controller(
    tmp_path: Path,
    resolver: ControlledResolver | None = None,
    voice: FakeVoice | None = None,
) -> tuple[Session, ControlledResolver, FakeVoice, Path]:
    actual_resolver = resolver or ControlledResolver()
    actual_voice = voice or FakeVoice()
    database = tmp_path / "player.sqlite3"
    controller = await Session.create(
        lambda: SQLiteStore(database), actual_resolver, actual_voice
    )
    return controller, actual_resolver, actual_voice, database


def test_queue_edits_are_persisted_and_status_is_immutable(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, _, voice, database = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        third = QueueEntry("https://youtu.be/third")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.enqueue(third)
            await controller.move_before(third.id, first.id)
            await controller.remove(second.id)
            status = controller.status
            assert status.player.upcoming == (third, first)
            assert status.volume == 1.0
            assert controller.channels() == (
                VoiceChannelInfo(7, "Music", True, True, 1, "Test server"),
            )
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            assert store.load().upcoming == (third, first)
        assert voice.disconnect_count == 1

    asyncio.run(scenario())


def test_pause_resume_volume_clear_and_stop_have_distinct_effects(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(
                lambda: controller.snapshot.state is PlaybackState.PLAYING
            )

            await controller.pause()
            assert controller.snapshot.state is PlaybackState.PAUSED
            await controller.play()
            await controller.set_volume(0.25)
            resumed = controller.snapshot
            assert resumed.state is PlaybackState.PLAYING
            assert (voice.pause_count, voice.resume_count, voice.volumes) == (
                1,
                1,
                [0.25],
            )

            await controller.clear()
            assert controller.snapshot.current == first
            assert controller.snapshot.upcoming == ()
            await controller.stop()
            assert controller.snapshot == PlayerSnapshot(
                upcoming=(first,),
                voice_state=VoiceState.CONNECTED,
                recently_played=resumed.recently_played,
            )
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_natural_completion_advances_to_next_track(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        entries = tuple(QueueEntry(f"https://youtu.be/{name}") for name in ("a", "b"))
        try:
            for entry in entries:
                await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0, "first")
            await _wait_until(lambda: len(voice.callbacks) == 1)
            voice.complete(0)
            await _wait_until(lambda: len(resolver.requests) == 2)
            assert controller.snapshot.current == entries[1]
            assert controller.snapshot.upcoming == ()
            resolver.succeed(1, "second")
            await _wait_until(
                lambda: controller.snapshot.state is PlaybackState.PLAYING
            )
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_completion_racing_with_skip_advances_only_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        entries = tuple(
            QueueEntry(f"https://youtu.be/{name}") for name in ("a", "b", "c")
        )
        try:
            for entry in entries:
                await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(lambda: len(voice.callbacks) == 1)

            voice.complete(0)
            await controller.skip()
            await _wait_until(lambda: len(resolver.requests) == 2)
            assert controller.snapshot.current == entries[1]
            assert controller.snapshot.upcoming == (entries[2],)
            assert resolver.calls == [entries[0].source_url, entries[1].source_url]
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_stale_completion_cannot_skip_successor(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(lambda: len(voice.callbacks) == 1)
            await controller.skip()
            await _wait_until(lambda: len(resolver.requests) == 2)
            resolver.succeed(1)
            await _wait_until(lambda: len(voice.callbacks) == 2)
            await _wait_until(
                lambda: controller.snapshot.state is PlaybackState.PLAYING
            )

            voice.complete(0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert controller.snapshot.current == second
            assert controller.snapshot.state is PlaybackState.PLAYING
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_stop_cancels_extraction_before_returning(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        entry = QueueEntry("https://youtu.be/first")
        try:
            await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            await controller.stop()
            assert resolver.cancelled == [entry.source_url]
            assert controller.snapshot.current is None
            assert controller.snapshot.upcoming == (entry,)
            assert voice.stop_count >= 1
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_late_result_is_ignored_when_resolver_suppresses_cancellation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        late = ResolvedTrack("https://stream.invalid/late")
        resolver = ControlledResolver(cancelled_result=late)
        controller, _, voice, _ = await _controller(tmp_path, resolver)
        entry = QueueEntry("https://youtu.be/first")
        try:
            await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            await controller.stop()
            await asyncio.sleep(0)
            assert resolver.cancelled == [entry.source_url]
            assert voice.played == []
            assert controller.snapshot.upcoming == (entry,)
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_transient_failure_retries_once_with_fresh_attempt_then_skips(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, _, _ = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            initial_attempt = controller.status.attempt_id
            resolver.fail(0, "temporary", retryable=True)
            await _wait_until(lambda: len(resolver.requests) == 2)
            retry_attempt = controller.status.attempt_id
            assert initial_attempt is not None and retry_attempt != initial_attempt
            assert resolver.calls[:2] == [first.source_url, first.source_url]
            assert controller.status.last_issue is None

            resolver.fail(1, "still temporary", retryable=True)
            await _wait_until(lambda: len(resolver.requests) == 3)
            assert resolver.calls == [
                first.source_url,
                first.source_url,
                second.source_url,
            ]
            assert controller.snapshot.current == second
            assert controller.status.last_issue is not None
            assert controller.status.last_issue.entry_id == first.id
            public_issue = State.from_status(controller.status).last_issue
            assert public_issue is not None and public_issue.entry is not None
            assert public_issue.entry.id == first.id
            assert public_issue.reason == "stream_interrupted"
            assert "still temporary" not in public_issue.model_dump_json()
            assert (
                State.from_status(await controller.read_status()).last_issue
                == public_issue
            )
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_permanent_failure_skips_without_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, _, _ = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.fail(0, "private video")
            await _wait_until(lambda: len(resolver.requests) == 2)
            assert resolver.calls == [first.source_url, second.source_url]
            assert controller.snapshot.current == second
            assert controller.status.last_issue is not None
            assert controller.status.last_issue.entry == first
            assert controller.status.last_issue.reason == "source_unavailable"
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_connection_loss_preserves_current_for_manual_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        entry = QueueEntry("https://youtu.be/first")
        try:
            await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(
                lambda: controller.snapshot.state is PlaybackState.PLAYING
            )
            voice.lose_connection()
            history = controller.snapshot.recently_played
            await _wait_until(
                lambda: controller.snapshot.voice_state is VoiceState.DISCONNECTED
            )
            assert controller.snapshot == PlayerSnapshot(
                state=PlaybackState.LOADING, current=entry, recently_played=history
            )
            assert controller.status.last_issue is not None
            assert "connection lost" in controller.status.last_issue.message

            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 2)
            assert controller.snapshot.current == entry
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_explicit_leave_preserves_current_without_loss_issue(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, _, _ = await _controller(tmp_path)
        entry = QueueEntry("https://youtu.be/first")
        try:
            await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            await controller.disconnect()
            assert controller.snapshot == PlayerSnapshot(
                state=PlaybackState.LOADING, current=entry
            )
            assert controller.status.last_issue is None
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 2)
            assert controller.snapshot.current == entry
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_ready_commit_failure_stops_audio_and_preserves_current(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            with closing(sqlite3.connect(database, autocommit=True)) as connection:
                connection.execute(
                    """CREATE TRIGGER reject_ready BEFORE UPDATE OF state ON player_state
                       WHEN NEW.state = 'playing'
                       BEGIN SELECT RAISE(ABORT, 'injected ready failure'); END"""
                )
            resolver.succeed(0)
            await _wait_until(
                lambda: (
                    controller.status.last_issue is not None
                    and controller.status.last_issue.fatal
                )
            )
            assert voice.played == [ResolvedTrack("https://stream.invalid/audio")]
            assert voice.stop_count >= 1
            assert controller.snapshot.state is PlaybackState.LOADING
            assert controller.snapshot.current == first
            assert controller.snapshot.upcoming == (second,)
            assert resolver.calls == [first.source_url]
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            persisted = store.load()
        assert persisted.state is PlaybackState.LOADING
        assert persisted.current == first
        assert persisted.upcoming == (second,)

    asyncio.run(scenario())


def test_operational_resolver_failure_persists_error_without_consuming_queue(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.requests[0].set_exception(RuntimeError("child cleanup failed"))
            await _wait_until(
                lambda: (
                    controller.status.last_issue is not None
                    and controller.status.last_issue.fatal
                )
            )
            assert controller.snapshot.state is PlaybackState.ERROR
            assert controller.snapshot.current == first
            assert controller.snapshot.upcoming == (second,)
            assert controller.status.attempt_id is None
            assert voice.played == []
            with pytest.raises(RuntimeError, match="operational failure"):
                await controller.play()
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            persisted = store.load()
        assert persisted.state is PlaybackState.ERROR
        assert persisted.current == first
        assert persisted.upcoming == (second,)

    asyncio.run(scenario())


def test_cancelling_caller_does_not_release_serialization_before_commit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, _, _, database = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            with closing(sqlite3.connect(database, autocommit=True)) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                first_call = asyncio.create_task(controller.enqueue(first))
                await asyncio.sleep(0)
                first_call.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first_call
                second_call = asyncio.create_task(controller.enqueue(second))
                await asyncio.sleep(0)
                assert not second_call.done()
                blocker.execute("ROLLBACK")
                await asyncio.wait_for(second_call, timeout=2)
            assert controller.snapshot.upcoming == (first, second)
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_close_is_idempotent_cancels_load_and_restores_current(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        entry = QueueEntry("https://youtu.be/first")
        await controller.enqueue(entry)
        await controller.connect(7)
        await controller.play()
        await _wait_until(lambda: len(resolver.requests) == 1)

        disconnects_before_close = voice.disconnect_count
        await asyncio.gather(controller.close(), controller.close())
        await controller.close()
        assert resolver.cancelled == [entry.source_url]
        assert controller.snapshot.current == entry
        assert controller.snapshot.state is PlaybackState.LOADING
        assert voice.disconnect_count == disconnects_before_close + 1
        with SQLiteStore(database) as store:
            assert store.load().current == entry
            checkpoint = store.checkpoint()
            assert checkpoint is not None
            assert checkpoint.channel_id == 7
            assert checkpoint.entry_id == entry.id
        with pytest.raises(RuntimeError, match="closed"):
            await controller.enqueue(QueueEntry("https://youtu.be/late"))

    asyncio.run(scenario())
