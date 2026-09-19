# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend import commands
from nahormaar_backend.audio import VoiceError
from nahormaar_backend.commands import Outcome, Receipt
from nahormaar_backend.models import PlaybackState, QueueEntry
from nahormaar_backend.playback import PlaybackController, PlaybackStatus
from nahormaar_backend.storage import SQLiteStore, StorageError
from test_playback import ControlledResolver, FakeVoice


async def wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


def test_concurrent_requests_replay_and_queue_conflicts(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = await PlaybackController.create(
            tmp_path / "player.sqlite3", ControlledResolver(), FakeVoice()
        )
        try:
            initial = await controller.read_status()
            ids = [uuid4(), uuid4()]
            add = commands.Add("https://youtu.be/Pqp9fDRp1lw")
            replies = await asyncio.gather(*(controller.request(i, add) for i in ids))
            state = await controller.read_status()
            assert [entry.id for entry in state.player.upcoming] == [
                reply.outcome.entry_id for reply in replies
            ]
            assert state.queue_revision == initial.queue_revision + 2
            duplicate, conflict = await asyncio.gather(
                controller.request(ids[0], add),
                controller.request(ids[0], commands.Volume(0.5)),
            )
            assert duplicate.replayed and duplicate.outcome == replies[0].outcome
            assert conflict.outcome.code == "idempotency_conflict"
            assert (await controller.read_status()).revision == state.revision
            stale = await controller.request(
                uuid4(), commands.Clear(initial.queue_revision)
            )
            assert stale.outcome.code == "queue_conflict"
            assert stale.status.player.upcoming == state.player.upcoming
            await controller.request(uuid4(), commands.Volume(0.5))
            assert (
                await controller.read_status()
            ).queue_revision == state.queue_revision
            moved = await controller.request(
                uuid4(),
                commands.Move(
                    state.player.upcoming[1].id,
                    state.player.upcoming[0].id,
                    state.queue_revision,
                ),
            )
            assert moved.outcome.code == "ok"
            assert moved.status.player.upcoming == tuple(
                reversed(state.player.upcoming)
            )
            noop = await controller.request(
                uuid4(),
                commands.Move(
                    moved.status.player.upcoming[0].id,
                    moved.status.player.upcoming[0].id,
                    moved.status.queue_revision,
                ),
            )
            assert noop.status.revision == moved.status.revision
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_storage_failure_while_reporting_voice_failure_closes_events(
    tmp_path: Path,
) -> None:
    class BrokenVoice(FakeVoice):
        def set_volume(self, volume: float) -> None:
            raise VoiceError("output failed")

    async def scenario() -> None:
        path = tmp_path / "player.sqlite3"
        controller = await PlaybackController.create(
            path, ControlledResolver(), BrokenVoice()
        )
        try:
            async with controller.subscribe() as events:
                await events.get()
                with closing(sqlite3.connect(path, autocommit=True)) as db:
                    db.execute("""CREATE TRIGGER reject_revision BEFORE UPDATE ON player_state
                                  BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
                with pytest.raises(StorageError):
                    await controller.set_volume(0.2)
                assert await events.get() is None
                assert controller.status.last_issue is not None
                assert controller.status.last_issue.fatal
                with pytest.raises(RuntimeError):
                    await controller.read_status()
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("finish_first", [False, True])
def test_two_targeted_skips_racing_completion_advance_once(
    tmp_path: Path, finish_first: bool
) -> None:
    async def scenario() -> None:
        resolver, voice = ControlledResolver(), FakeVoice()
        controller = await PlaybackController.create(
            tmp_path / "player.sqlite3", resolver, voice
        )
        try:
            entries = [QueueEntry(f"https://youtu.be/{i}") for i in range(3)]
            for entry in entries:
                await controller.enqueue(entry)
            await controller.connect(7)
            await controller.play()
            await wait_for(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await wait_for(lambda: controller.snapshot.state is PlaybackState.PLAYING)
            state = await controller.read_status()
            skip = commands.Control("skip", state.attempt_id)
            if finish_first:
                voice.complete(0)
                await wait_for(lambda: controller.snapshot.current == entries[1])
            key = uuid4()
            first, second, repeat = await asyncio.gather(
                controller.request(key, skip),
                controller.request(uuid4(), skip),
                controller.request(key, skip),
            )
            if not finish_first:
                voice.complete(0)
            assert repeat.replayed and repeat.outcome == first.outcome
            assert second.outcome.code == "playback_conflict"
            await asyncio.sleep(0.01)
            assert controller.snapshot.current == entries[1]
            assert controller.snapshot.upcoming == (entries[2],)
            stale_stop = await controller.request(
                uuid4(), commands.Control("stop", state.attempt_id)
            )
            assert stale_stop.outcome.code == "playback_conflict"
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_completed_and_interrupted_requests_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "player.sqlite3"
    add_id, pending_id, volume_id = uuid4(), uuid4(), uuid4()
    add = commands.Add("https://youtu.be/Pqp9fDRp1lw")
    volume = commands.Volume(0.25)

    async def scenario() -> None:
        first = await PlaybackController.create(path, ControlledResolver(), FakeVoice())
        result = await first.request(add_id, add)
        await first.request(volume_id, volume)
        revision = (await first.read_status()).revision
        await first.close()
        with SQLiteStore(path) as store:
            assert store.reserve(Receipt(pending_id, commands.fingerprint(add))) is None
        voice = FakeVoice()
        second = await PlaybackController.create(path, ControlledResolver(), voice)
        try:
            replay = await second.request(add_id, add)
            assert replay.replayed and replay.outcome == result.outcome
            assert len(replay.status.player.upcoming) == 1
            assert replay.status.revision > revision
            pending = await second.request(pending_id, add)
            assert pending.replayed and pending.outcome == Outcome("interrupted", 409)
            replay_volume = await second.request(volume_id, volume)
            assert replay_volume.replayed and replay_volume.status.volume == 1
            assert voice.volumes == []
        finally:
            await second.close()

    asyncio.run(scenario())


def test_cancelled_request_remains_owned_and_retry_waits_for_result(
    tmp_path: Path,
) -> None:
    class SlowVoice(FakeVoice):
        started: asyncio.Event
        release: asyncio.Event

        async def connect(self, channel_id: int) -> None:
            self.started.set()
            await self.release.wait()
            await super().connect(channel_id)

    async def scenario() -> None:
        voice = SlowVoice()
        voice.started, voice.release = asyncio.Event(), asyncio.Event()
        controller = await PlaybackController.create(
            tmp_path / "player.sqlite3", ControlledResolver(), voice
        )
        key, command = uuid4(), commands.Connect(7)
        try:
            request = asyncio.create_task(controller.request(key, command))
            await voice.started.wait()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            retry = asyncio.create_task(controller.request(key, command))
            await asyncio.sleep(0)
            assert not retry.done()
            voice.release.set()
            result = await retry
            assert result.replayed and result.outcome.code == "ok"
            assert result.status.channel_id == 7
        finally:
            voice.release.set()
            await controller.close()

    asyncio.run(scenario())


def test_failed_commit_never_broadcasts_success_or_completes_receipt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "player.sqlite3"
    key = uuid4()
    command = commands.Add("https://youtu.be/Pqp9fDRp1lw")

    async def scenario() -> None:
        controller = await PlaybackController.create(
            path, ControlledResolver(), FakeVoice()
        )
        try:
            async with controller.subscribe() as events:
                before = await events.get()
                with closing(sqlite3.connect(path, autocommit=True)) as db:
                    db.execute("""CREATE TRIGGER reject_entry BEFORE INSERT ON queue_entries
                                  BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
                with pytest.raises(StorageError):
                    await controller.request(key, command)
                assert await events.get() is None
                assert before is not None
                with SQLiteStore(path) as store:
                    assert store.load().upcoming == ()
                    assert store.revisions().revision == before.revision
                    receipt = store.reserve(Receipt(key, commands.fingerprint(command)))
                    assert receipt is not None and receipt.outcome is None
                with pytest.raises(RuntimeError):
                    await controller.read_status()
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_subscribers_receive_latest_committed_snapshot_and_reconnect(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller = await PlaybackController.create(
            tmp_path / "player.sqlite3", ControlledResolver(), FakeVoice()
        )
        try:
            async with controller.subscribe() as first, controller.subscribe() as slow:
                assert await first.get() == await slow.get()
                latest: PlaybackStatus | None = await controller.read_status()
                for i in range(6):
                    await controller.enqueue(QueueEntry(f"https://youtu.be/{i}"))
                    latest = await first.get()
                assert slow.qsize() == 1
                assert await slow.get() == latest
                async with controller.subscribe() as reconnect:
                    assert await reconnect.get() == await controller.read_status()
                await controller.close()
                assert await first.get() is None
                assert await slow.get() is None
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_progress_anchors_change_on_pause_resume_without_changing_queue_revision(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        resolver, voice = ControlledResolver(), FakeVoice()
        controller = await PlaybackController.create(
            tmp_path / "player.sqlite3", resolver, voice
        )
        try:
            await controller.enqueue(QueueEntry("https://youtu.be/Pqp9fDRp1lw"))
            await controller.connect(7)
            await controller.play()
            await wait_for(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await wait_for(lambda: controller.snapshot.state is PlaybackState.PLAYING)
            playing = await controller.read_status()
            await asyncio.sleep(0.02)
            await controller.pause()
            paused = await controller.read_status()
            assert paused.position_seconds > playing.position_seconds
            assert paused.queue_revision == playing.queue_revision
            await controller.set_volume(0.2)
            assert (
                await controller.read_status()
            ).position_updated_at == paused.position_updated_at
            await controller.play()
            resumed = await controller.read_status()
            assert resumed.attempt_id == paused.attempt_id
            assert resumed.position_seconds == paused.position_seconds
            assert resumed.position_updated_at != paused.position_updated_at
        finally:
            await controller.close()

    asyncio.run(scenario())
