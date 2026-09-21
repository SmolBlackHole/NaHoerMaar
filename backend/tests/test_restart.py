# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from nahormaar_backend.application import session as playback
from nahormaar_backend.application.audio import ResolvedTrack, VoiceError
from nahormaar_backend.application.session import Session
from nahormaar_backend.application.player import Player
from nahormaar_backend.domain.checkpoint import PlaybackCheckpoint
from nahormaar_backend.domain.models import (
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    VoiceState,
)
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError
from test_crossfade import _playing, _wait_for_started
from test_playback import ControlledResolver, FakeVoice, _controller, _wait_until


async def start_track(
    controller: Session, resolver: ControlledResolver
) -> tuple[QueueEntry, QueueEntry]:
    entries = (
        QueueEntry("https://youtu.be/first"),
        QueueEntry("https://youtu.be/second"),
    )
    for entry in entries:
        await controller.enqueue(entry)
    await controller.connect(7)
    await controller.play()
    await resolve(resolver)
    await _wait_until(lambda: controller.snapshot.state is PlaybackState.PLAYING)
    return entries


async def resolve(resolver: ControlledResolver, index: int = 0) -> None:
    await _wait_until(lambda: len(resolver.requests) > index)
    resolver.requests[index].set_result(
        ResolvedTrack("fresh-stream", duration_seconds=120, title="A song")
    )


@pytest.mark.parametrize("paused", [False, True])
def test_restart_resumes_channel_position_volume_and_history(
    tmp_path: Path, paused: bool
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        try:
            entries = await start_track(controller, resolver)
            await controller.set_volume(0.4)
            voice.position_seconds = 42.36
            if paused:
                await controller.pause()
            history = controller.snapshot.recently_played
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            assert store.checkpoint() == PlaybackCheckpoint(
                7, entries[0].id, 42.36, paused, 0.4, history_recorded=True
            )

        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            assert not voice.connected
            assert not resolver.calls
            assert restored.snapshot.state is PlaybackState.LOADING
            await restored.restore()
            await restored.restore()  # Startup restoration is idempotent.
            await resolve(resolver)
            expected = PlaybackState.PAUSED if paused else PlaybackState.PLAYING
            await _wait_until(
                lambda: restored.snapshot.state is expected and bool(voice.positions)
            )
            assert resolver.calls == [entries[0].source_url]
            assert voice.channel_id == 7
            assert voice.positions == [42.36]
            assert voice.pause_count == int(paused)
            assert voice.volumes == [0.4]
            assert restored.status.position_seconds == 42.36
            assert restored.snapshot.current is not None
            assert restored.snapshot.current.id == entries[0].id
            assert restored.snapshot.upcoming == (entries[1],)
            assert restored.snapshot.recently_played == history
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_stopped_player_rejoins_without_starting_the_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, resolver, _, _ = await _controller(tmp_path)
        try:
            await start_track(controller, resolver)
            await controller.stop()
            queued = controller.snapshot.upcoming
        finally:
            await controller.close()
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            await restored.restore()
            assert voice.channel_id == 7
            assert restored.snapshot.state is PlaybackState.IDLE
            assert restored.snapshot.upcoming == queued
            assert not resolver.calls
            assert not voice.played
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_explicit_leaving_retains_memory_but_clears_automatic_rejoin(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        try:
            await start_track(controller, resolver)
            current = controller.snapshot.current
            assert current is not None
            voice.position_seconds = 32
            await controller.disconnect()
            assert controller.snapshot.current == current
            assert controller.snapshot.state is PlaybackState.LOADING
            assert controller.status.position_seconds == 32
            queued = (current, *controller.snapshot.upcoming)
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            assert store.checkpoint() is None
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            await restored.restore()
            assert not voice.connected
            assert not resolver.calls
            assert restored.snapshot.upcoming == queued
        finally:
            await restored.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("paused", [False, True])
def test_connection_loss_keeps_checkpoint_for_process_restoration(
    tmp_path: Path, paused: bool
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        try:
            entries = await start_track(controller, resolver)
            voice.position_seconds = 37.5
            if paused:
                await controller.pause()
            history = controller.snapshot.recently_played
            voice.lose_connection()
            await _wait_until(
                lambda: controller.snapshot.voice_state is VoiceState.DISCONNECTED
            )
            assert controller.snapshot.current is not None
            assert controller.snapshot.current.id == entries[0].id
            assert controller.snapshot.upcoming == entries[1:]
            assert len(resolver.calls) == 1
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            assert store.checkpoint() == PlaybackCheckpoint(
                7, entries[0].id, 37.5, paused, history_recorded=True
            )
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            await restored.restore()
            await resolve(resolver)
            expected = PlaybackState.PAUSED if paused else PlaybackState.PLAYING
            await _wait_until(
                lambda: restored.snapshot.state is expected and bool(voice.positions)
            )
            assert voice.connected and voice.channel_id == 7
            assert voice.positions == [37.5]
            assert resolver.calls == [entries[0].source_url]
            assert restored.snapshot.upcoming == entries[1:]
            assert restored.snapshot.recently_played == history
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_restart_while_loading_records_history_only_when_audio_starts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, _, _, _ = await _controller(tmp_path)
        entry = QueueEntry("https://youtu.be/first")
        try:
            await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
        finally:
            await controller.close()
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            assert not restored.snapshot.recently_played
            await restored.restore()
            await resolve(resolver)
            await _wait_until(lambda: restored.snapshot.state is PlaybackState.PLAYING)
            assert voice.positions == [0]
            assert len(restored.snapshot.recently_played) == 1
            assert restored.snapshot.recently_played[0].entry.id == entry.id
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_failed_startup_before_gateway_ready_preserves_checkpoint(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        try:
            await start_track(controller, resolver)
            voice.position_seconds = 23.5
            await controller.pause()
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            checkpoint = store.checkpoint()
        interrupted, _, _, _ = await _controller(tmp_path)
        await interrupted.close()  # Discord login failed before restore could run.
        with SQLiteStore(database) as store:
            assert store.checkpoint() == checkpoint
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            await restored.restore()
            await resolve(resolver)
            await _wait_until(
                lambda: (
                    restored.snapshot.state is PlaybackState.PAUSED
                    and bool(voice.positions)
                )
            )
            assert voice.positions == [23.5]
            assert len(restored.snapshot.recently_played) == 1
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_missing_channel_keeps_queue_and_reports_issue(tmp_path: Path) -> None:
    class MissingChannel(FakeVoice):
        async def connect(self, channel_id: int) -> None:
            raise VoiceError("The saved channel is unavailable.")

    async def scenario() -> None:
        controller, resolver, _, database = await _controller(tmp_path)
        try:
            entries = await start_track(controller, resolver)
        finally:
            await controller.close()
        with SQLiteStore(database) as store:
            checkpoint = store.checkpoint()
        restored, resolver, voice, _ = await _controller(
            tmp_path, voice=MissingChannel()
        )
        try:
            await restored.restore()
            assert not voice.connected
            assert not resolver.calls
            status = await restored.read_status()
            assert status.player.current is not None
            assert status.player.current.id == entries[0].id
            assert status.player.state is PlaybackState.LOADING
            assert status.player.voice_state is VoiceState.DISCONNECTED
            assert tuple(entry.id for entry in status.player.upcoming) == tuple(
                entry.id for entry in entries[1:]
            )
            assert status.last_issue is not None
            assert status.last_issue.message == "The saved channel is unavailable."
            with SQLiteStore(database) as store:
                assert store.checkpoint() == checkpoint
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_crash_uses_periodic_checkpoint_without_a_clean_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playback, "CHECKPOINT_INTERVAL_SECONDS", 0.01)

    async def scenario() -> None:
        controller, resolver, voice, database = await _controller(tmp_path)
        crash_database = tmp_path / "crash.sqlite3"
        try:
            await start_track(controller, resolver)
            revision = (await controller.read_status()).revision
            voice.position_seconds = 19.74
            async with asyncio.timeout(2):
                while True:
                    with closing(sqlite3.connect(database)) as connection:
                        row = connection.execute(
                            "SELECT position_seconds FROM playback_checkpoint"
                        ).fetchone()
                        if row is not None and row[0] == 19.74:
                            with closing(sqlite3.connect(crash_database)) as target:
                                connection.backup(target)
                            break
                    await asyncio.sleep(0.005)
            assert (await controller.read_status()).revision == revision
            voice.position_seconds = 24  # The crash copy has no shutdown checkpoint.
        finally:
            await controller.close()
        resolver, voice = ControlledResolver(), FakeVoice()
        restored = await Session.create(
            lambda: SQLiteStore(crash_database), resolver, voice
        )
        try:
            await restored.restore()
            await resolve(resolver)
            await _wait_until(lambda: restored.snapshot.state is PlaybackState.PLAYING)
            assert voice.positions == [19.74]
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_restart_during_crossfade_resumes_incoming_track(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, _, voice, _, entries = await _playing(tmp_path)
        try:
            voice.fade_due()
            await _wait_for_started(controller, voice, 2)
            await controller.read_status()  # Wait for the serialized transition commit.
            voice.position_seconds = 2.5
            history = controller.snapshot.recently_played
        finally:
            await controller.close()
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            await restored.restore()
            await resolve(resolver)
            await _wait_until(lambda: restored.snapshot.state is PlaybackState.PLAYING)
            assert resolver.calls[0] == entries[1].source_url
            assert voice.positions == [2.5]
            assert restored.snapshot.current is not None
            assert restored.snapshot.current.id == entries[1].id
            assert restored.snapshot.upcoming == entries[2:]
            assert restored.snapshot.recently_played == history
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_retry_of_paused_restoration_keeps_offset_and_does_not_count_a_play(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        try:
            await start_track(controller, resolver)
            voice.position_seconds = 41
            await controller.pause()
        finally:
            await controller.close()
        restored, resolver, voice, _ = await _controller(tmp_path)
        try:
            await restored.restore()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.fail(0, "temporary failure", retryable=True)
            await resolve(resolver, 1)
            await _wait_until(
                lambda: (
                    restored.snapshot.state is PlaybackState.PAUSED
                    and bool(voice.positions)
                )
            )
            assert voice.positions == [41]
            assert len(restored.snapshot.recently_played) == 1
        finally:
            await restored.close()

        with SQLiteStore(tmp_path / "player.sqlite3") as store:
            checkpoint = store.checkpoint()
            assert checkpoint is not None
            assert checkpoint.position_seconds == 41
            assert checkpoint.paused

    asyncio.run(scenario())


def test_checkpoint_rejects_another_entry_without_overwriting_saved_position(
    store: SQLiteStore,
) -> None:
    checkpoint = PlaybackCheckpoint(7, None)
    store.save_checkpoint(checkpoint)
    with pytest.raises(StorageError, match="Cannot save playback checkpoint"):
        store.save_checkpoint(PlaybackCheckpoint(7, QueueEntry("other").id))
    assert store.checkpoint() == checkpoint


def test_track_changes_update_checkpoint_in_the_same_transaction(
    store: SQLiteStore, player: Player
) -> None:
    first, second = QueueEntry("first"), QueueEntry("second")
    checkpoint = PlaybackCheckpoint(7, first.id, 35, volume=0.4, history_recorded=True)
    player.commit_lifecycle(
        PlayerSnapshot(PlaybackState.PLAYING, first, (second,)),
        checkpoint,
        record_history=True,
    )
    history = player.snapshot.recently_played
    player.commit_lifecycle(
        replace(player.snapshot, state=PlaybackState.PAUSED),
        replace(checkpoint, paused=True),
    )
    assert store.load() == player.snapshot
    assert store.checkpoint() == replace(checkpoint, paused=True)
    player.commit_lifecycle(
        replace(
            player.snapshot, state=PlaybackState.LOADING, current=second, upcoming=()
        ),
        PlaybackCheckpoint(7, second.id, volume=0.4),
    )
    assert store.load() == player.snapshot
    assert store.checkpoint() == PlaybackCheckpoint(7, second.id, volume=0.4)
    player.commit_lifecycle(
        replace(
            player.snapshot, state=PlaybackState.IDLE, current=None, upcoming=(second,)
        ),
        PlaybackCheckpoint(7, None, volume=0.4),
    )
    assert store.load() == player.snapshot
    assert store.checkpoint() == PlaybackCheckpoint(7, None, volume=0.4)
    assert store.load().recently_played == history
