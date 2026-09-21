# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend.application.audio import ResolvedTrack, TrackError
from nahormaar_backend.application.session import Session
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.crossfade import fade_duration
from nahormaar_backend.domain.models import PlaybackState, PlayerSnapshot, QueueEntry
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError
from test_api import Harness, headers, mutation
from test_playback import ControlledResolver, FakeVoice, _controller, _wait_until


async def _playing(
    tmp_path: Path,
) -> tuple[Session, ControlledResolver, FakeVoice, Path, tuple[QueueEntry, ...]]:
    controller, resolver, voice, database = await _controller(tmp_path)
    entries = tuple(QueueEntry(f"https://youtu.be/track-{i}") for i in range(3))
    for entry in entries:
        await controller.enqueue(entry)
    await controller.request(uuid4(), commands.Crossfade(5))
    await controller.connect(7)
    await controller.play()
    await _wait_until(lambda: len(resolver.requests) == 1)
    resolver.requests[0].set_result(ResolvedTrack("first", duration_seconds=20))
    await _wait_until(lambda: len(resolver.requests) == 2)
    resolver.requests[1].set_result(ResolvedTrack("second", duration_seconds=20))
    await _wait_until(lambda: voice.prepared is not None)
    return controller, resolver, voice, database, entries


async def _wait_for_started(controller: Session, voice: FakeVoice, count: int) -> None:
    await _wait_until(
        lambda: (
            len(voice.played) == count
            and controller.snapshot.state is PlaybackState.PLAYING
        )
    )


def test_crossfade_changes_current_once_and_ignores_old_completion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database, entries = await _playing(tmp_path)
        try:
            assert len(controller.snapshot.recently_played) == 1
            with SQLiteStore(database) as store:
                assert store.load().upcoming == entries[1:]
            previous = controller.status.attempt_id
            voice.fade_due()
            await _wait_for_started(controller, voice, 2)
            assert controller.snapshot.current is not None
            assert controller.snapshot.current.id == entries[1].id
            assert controller.status.attempt_id != previous
            assert controller.status.position_seconds == 0
            assert len(controller.snapshot.recently_played) == 2
            assert controller.snapshot.upcoming == entries[2:]
            assert len(resolver.calls) == 2  # No third source during overlap.
            voice.complete(0)
            await controller.read_status()
            assert controller.snapshot.current is not None
            assert controller.snapshot.current.id == entries[1].id
            voice.transitioning = False
            voice.fade_finished()
            await _wait_until(lambda: len(resolver.calls) == 3)
            with SQLiteStore(database) as store:
                snapshot = store.load()
                assert snapshot.current is not None
                assert snapshot.current.id == entries[1].id
                assert len(snapshot.recently_played) == 2
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["remove", "reorder", "disable", "stop"])
def test_prepared_entry_is_invalidated_before_start(
    tmp_path: Path, operation: str
) -> None:
    async def scenario() -> None:
        controller, _, voice, _, entries = await _playing(tmp_path)
        old_due = voice.fade_due
        try:
            if operation == "remove":
                await controller.remove(entries[1].id)
            elif operation == "reorder":
                await controller.move_before(entries[2].id, entries[1].id)
            elif operation == "disable":
                await controller.request(uuid4(), commands.Crossfade(0))
            else:
                await controller.stop()
            old_due()
            await asyncio.sleep(0.02)
            assert len(voice.played) == 1
            assert len(controller.snapshot.recently_played) == 1
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_pause_defers_due_fade_until_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, _, voice, _, _ = await _playing(tmp_path)
        try:
            await controller.pause()
            voice.fade_due()
            await asyncio.sleep(0.02)
            assert len(voice.played) == 1
            await controller.play()
            await _wait_for_started(controller, voice, 2)
            await controller.pause()
            assert voice.pause_count == 2
            assert controller.snapshot.state is PlaybackState.PAUSED
            await controller.play()
            assert voice.resume_count == 2
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["skip", "seek", "stop", "disconnect"])
def test_transport_during_overlap_controls_incoming_once(
    tmp_path: Path, action: str
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _, entries = await _playing(tmp_path)
        try:
            voice.fade_due()
            await _wait_for_started(controller, voice, 2)
            attempt = controller.status.attempt_id
            if action == "seek":
                assert attempt is not None
                await controller.request(uuid4(), commands.Seek(8, attempt))
                assert voice.positions[-1] == 8
                assert controller.snapshot.current is not None
                assert controller.snapshot.current.id == entries[1].id
            elif action == "skip":
                await controller.skip()
                await _wait_until(lambda: len(resolver.calls) == 3)
                assert controller.snapshot.current is not None
                assert controller.snapshot.current.id == entries[2].id
            elif action == "stop":
                await controller.stop()
                assert controller.snapshot.current is None
                assert controller.snapshot.upcoming[0].id == entries[1].id
            else:
                await controller.disconnect()
                assert controller.snapshot.current is not None
                assert controller.snapshot.current.id == entries[1].id
                assert controller.snapshot.state is PlaybackState.LOADING
                assert controller.snapshot.upcoming == entries[2:]
            voice.complete(0)
            voice.complete(1)
            await asyncio.sleep(0.02)
            assert len(controller.snapshot.recently_played) == 2
            assert not voice.transitioning
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_failed_preparation_keeps_current_and_uses_normal_transition(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        try:
            await controller.request(uuid4(), commands.Crossfade(5))
            for i in range(2):
                await controller.enqueue(QueueEntry(f"https://youtu.be/track-{i}"))
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.requests[0].set_result(ResolvedTrack("first", duration_seconds=20))
            await _wait_until(lambda: len(resolver.requests) == 2)
            resolver.fail(1, "unavailable")
            await asyncio.sleep(0.02)
            assert len(voice.played) == 1
            assert len(controller.snapshot.recently_played) == 1
            assert controller.status.last_issue is None
            voice.complete(0)
            await _wait_until(lambda: len(resolver.requests) == 3)
            resolver.succeed(2)
            await _wait_for_started(controller, voice, 2)
            assert len(controller.snapshot.recently_played) == 2
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_incoming_stream_retry_does_not_duplicate_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _, entries = await _playing(tmp_path)
        try:
            voice.fade_due()
            await _wait_for_started(controller, voice, 2)
            voice.complete(1, TrackError("interrupted", retryable=True))
            await _wait_until(lambda: len(resolver.requests) == 3)
            resolver.succeed(2)
            await _wait_for_started(controller, voice, 3)
            assert controller.snapshot.current is not None
            assert controller.snapshot.current.id == entries[1].id
            assert len(controller.snapshot.recently_played) == 2
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_eof_during_commit_falls_back_without_losing_incoming(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, _, voice, _, entries = await _playing(tmp_path)
        try:
            # The adapter cannot activate once its old audio thread reached EOF.
            voice.prepared = None
            voice.fade_due()
            await _wait_for_started(controller, voice, 2)
            assert controller.snapshot.current is not None
            assert controller.snapshot.current.id == entries[1].id
            assert len(controller.snapshot.recently_played) == 2
            assert voice.stop_count > 0
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_database_failure_never_starts_prepared_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        controller, _, voice, _, entries = await _playing(tmp_path)
        original_save = SQLiteStore.save

        def fail_save(
            self: SQLiteStore,
            snapshot: PlayerSnapshot,
            *,
            checkpoint: PlaybackCheckpoint | None,
            revisions: commands.Revisions | None = None,
            receipt: commands.Receipt | None = None,
        ) -> None:
            if snapshot.current is not None and snapshot.current.id == entries[1].id:
                raise StorageError("test failure")
            original_save(
                self,
                snapshot,
                checkpoint=checkpoint,
                revisions=revisions,
                receipt=receipt,
            )

        monkeypatch.setattr(SQLiteStore, "save", fail_save)
        try:
            voice.fade_due()
            await _wait_until(lambda: controller.status.last_issue is not None)
            assert controller.status.last_issue is not None
            assert controller.status.last_issue.fatal
            await _wait_until(lambda: voice.prepared is None)
            assert len(voice.played) == 1
            assert voice.prepared is None
            assert len(controller.snapshot.recently_played) == 1
        finally:
            monkeypatch.setattr(SQLiteStore, "save", original_save)
            await controller.close()

    asyncio.run(scenario())


def test_crossfade_api_validation_replay_and_restart(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "player.sqlite3")

    async def scenario() -> None:
        async with harness.client() as client:
            assert (await client.get("/api/state")).json()["crossfade_seconds"] == 0
            request_headers = headers()
            for replayed in (False, True):
                result = mutation(
                    await client.put(
                        "/api/player/crossfade",
                        json={"seconds": 5},
                        headers=request_headers,
                    )
                )
                assert result.replayed is replayed
                assert result.snapshot.crossfade_seconds == 5
            for value in (-1, 1, 2, 8, 5.5, True, "5"):
                response = await client.put(
                    "/api/player/crossfade", json={"seconds": value}, headers=headers()
                )
                assert response.status_code == 422
        async with harness.client() as client:
            assert (await client.get("/api/state")).json()["crossfade_seconds"] == 5

    asyncio.run(scenario())


def test_short_and_unknown_durations() -> None:
    assert fade_duration(5, 100, 100) == 5
    assert fade_duration(7, 4, 20) == 2
    assert fade_duration(7, 20, 3) == 1.5
    assert fade_duration(5, 20, None) == 0
    assert fade_duration(5, None, 20) == 0
    assert fade_duration(0, 20, 20) == 0


def test_setting_change_during_fade_applies_after_tail_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _, _ = await _playing(tmp_path)
        try:
            voice.fade_due()
            await _wait_for_started(controller, voice, 2)
            await controller.request(uuid4(), commands.Crossfade(7))
            assert voice.transitioning
            assert voice.fade_seconds == 5
            assert len(resolver.requests) == 2
            voice.transitioning = False
            voice.fade_finished()
            await _wait_until(lambda: len(resolver.requests) == 3)
            resolver.requests[2].set_result(ResolvedTrack("third", duration_seconds=20))
            await _wait_until(lambda: voice.prepared is not None)
            assert voice.fade_seconds == 7
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_recovery_during_overlap_retains_incoming_and_setting(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database, entries = await _playing(tmp_path)
        voice.fade_due()
        await _wait_for_started(controller, voice, 2)
        await controller.close()
        reopened = await Session.create(lambda: SQLiteStore(database), resolver, voice)
        try:
            assert reopened.snapshot.state is PlaybackState.LOADING
            assert reopened.snapshot.crossfade_seconds == 5
            assert reopened.snapshot.current is not None
            assert reopened.snapshot.current.id == entries[1].id
            assert [entry.id for entry in reopened.snapshot.upcoming] == [entries[2].id]
            assert len(reopened.snapshot.recently_played) == 2
            assert not voice.transitioning
        finally:
            await reopened.close()

    asyncio.run(scenario())
