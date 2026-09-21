# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend.application import session as session_module
from nahormaar_backend.application.audio import (
    AudioCompleted,
    AudioEndReason,
    VoiceError,
)
from nahormaar_backend.application.playback import (
    CrossfadeReady,
    PlaybackMessage,
    PreparationFailed,
)
from nahormaar_backend.application.session import MetadataResolved, Request, Session
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.models import PlayerSnapshot, QueueEntry, TrackMetadata
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_playback import ControlledResolver, FakeVoice, _wait_until
from test_crossfade import _playing


class ConnectingVoice(FakeVoice):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def connect(self, channel_id: int) -> None:
        self.entered.set()
        await self.release.wait()
        await super().connect(channel_id)


async def session_with_gate(tmp_path: Path) -> tuple[Session, ConnectingVoice]:
    voice = ConnectingVoice()
    session = await Session.create(
        lambda: SQLiteStore(tmp_path / "inbox.sqlite3"), ControlledResolver(), voice
    )
    return session, voice


def test_bounded_ingress_preserves_commands_and_fifo_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "INBOX_CAPACITY", 2)

    async def scenario() -> None:
        session, voice = await session_with_gate(tmp_path)
        connecting = asyncio.create_task(session.connect(7))
        operations: list[asyncio.Task[PlayerSnapshot]] = []
        try:
            await asyncio.wait_for(voice.entered.wait(), 2)
            for volume in (0.1, 0.2, 0.3, 0.4):
                operations.append(asyncio.create_task(session.set_volume(volume)))
                await asyncio.sleep(0)
            assert len(session._inbox) == 2
            assert not any(operation.done() for operation in operations)
            voice.release.set()
            await asyncio.wait_for(asyncio.gather(connecting, *operations), 2)
            assert voice.volumes[-4:] == [0.1, 0.2, 0.3, 0.4]
            assert (await session.read_status()).volume == 0.4
        finally:
            voice.release.set()
            await session.close()
            await asyncio.gather(connecting, *operations, return_exceptions=True)

    asyncio.run(scenario())


def test_cancellation_before_admission_leaves_no_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "INBOX_CAPACITY", 1)

    async def scenario() -> None:
        session, voice = await session_with_gate(tmp_path)
        connecting = asyncio.create_task(session.connect(7))
        try:
            await asyncio.wait_for(voice.entered.wait(), 2)
            accepted = asyncio.create_task(session.set_volume(0.2))
            await _wait_until(lambda: len(session._inbox) == 1)
            waiting = asyncio.create_task(session.set_volume(0.8))
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            voice.release.set()
            await asyncio.wait_for(asyncio.gather(connecting, accepted), 2)
            assert (await session.read_status()).volume == 0.2
            assert 0.8 not in voice.volumes
        finally:
            voice.release.set()
            await session.close()

    asyncio.run(scenario())


def test_shutdown_rejects_queued_and_waiting_commands_without_stranding_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "INBOX_CAPACITY", 1)

    async def scenario() -> None:
        session, voice = await session_with_gate(tmp_path)
        connecting = asyncio.create_task(session.connect(7))
        closing: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(voice.entered.wait(), 2)
            accepted = asyncio.create_task(session.set_volume(0.2))
            await _wait_until(lambda: len(session._inbox) == 1)
            waiting = asyncio.create_task(session.set_volume(0.8))
            await asyncio.sleep(0)
            closing = asyncio.create_task(session.close())
            await _wait_until(lambda: session.state.closing)
            with pytest.raises(RuntimeError, match="closed"):
                await asyncio.wait_for(waiting, 2)
            voice.release.set()
            await asyncio.wait_for(connecting, 2)
            with pytest.raises(RuntimeError, match="closed"):
                await asyncio.wait_for(accepted, 2)
            await asyncio.wait_for(closing, 2)
            assert session._runner.done()
            assert not session._inbox
            assert 0.2 not in voice.volumes and 0.8 not in voice.volumes
        finally:
            voice.release.set()
            await session.close()
            if closing is not None:
                await closing

    asyncio.run(scenario())


def test_audio_callbacks_have_reserved_capacity_and_share_fifo_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "INBOX_CAPACITY", 1)
    monkeypatch.setattr(session_module, "CALLBACK_RESERVE", 1)

    async def scenario() -> None:
        session, voice = await session_with_gate(tmp_path)
        connecting = asyncio.create_task(session.connect(7))
        seen: list[float] = []
        handle = session.playback.handle

        async def observe(message: PlaybackMessage) -> None:
            seen.append(session.status.volume)
            await handle(message)

        monkeypatch.setattr(session.playback, "handle", observe)
        try:
            await asyncio.wait_for(voice.entered.wait(), 2)
            accepted = asyncio.create_task(session.set_volume(0.2))
            await _wait_until(lambda: len(session._inbox) == 1)
            session._post(AudioCompleted(uuid4(), AudioEndReason.NATURAL, 120))
            assert len(session._inbox) == 2
            voice.release.set()
            await asyncio.wait_for(asyncio.gather(connecting, accepted), 2)
            await session.read_status()
            assert seen == [0.2]
            assert not session.state.faulted
        finally:
            voice.release.set()
            await session.close()

    asyncio.run(scenario())


def test_callback_overflow_is_an_explicit_failure_not_silent_message_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(session_module, "INBOX_CAPACITY", 1)
    monkeypatch.setattr(session_module, "CALLBACK_RESERVE", 1)

    async def scenario() -> None:
        session, voice = await session_with_gate(tmp_path)
        connecting = asyncio.create_task(session.connect(7))
        try:
            await asyncio.wait_for(voice.entered.wait(), 2)
            accepted = asyncio.create_task(session.set_volume(0.2))
            await _wait_until(lambda: len(session._inbox) == 1)
            session._post(AudioCompleted(uuid4(), AudioEndReason.NATURAL, 120))
            session._post(AudioCompleted(uuid4(), AudioEndReason.NATURAL, 120))
            assert len(session._inbox) == 2
            voice.release.set()
            await asyncio.wait_for(connecting, 2)
            with pytest.raises(RuntimeError, match="operational failure"):
                await asyncio.wait_for(accepted, 2)
            assert session.state.faulted
            assert session.status.last_issue is not None
            assert "overloaded" in session.status.last_issue.message
            assert "session.inbox_overflow" in caplog.text
        finally:
            voice.release.set()
            await asyncio.wait_for(session.close(), 2)

    asyncio.run(scenario())


def test_metadata_for_removed_entry_is_not_applied_to_successor(tmp_path: Path) -> None:
    async def scenario() -> None:
        session, voice = await session_with_gate(tmp_path)
        first, second = (
            QueueEntry("https://youtu.be/first"),
            QueueEntry("https://youtu.be/second"),
        )
        try:
            await session.enqueue(first)
            await session.enqueue(second)
            await session.remove(first.id)
            before = await session.read_status()
            await session._send(
                MetadataResolved(first.id, TrackMetadata(title="Late first"))
            )
            assert await session.read_status() == before
            assert session.snapshot.upcoming == (second,)
        finally:
            voice.release.set()
            await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", ["remove", "reorder", "disable", "duration"])
@pytest.mark.parametrize("signal", ["due", "failed"])
def test_queued_audio_result_rechecks_identity_before_subscriber_refresh(
    tmp_path: Path, edit: str, signal: str
) -> None:
    async def scenario() -> None:
        session, _, voice, _, entries = await _playing(tmp_path)
        try:
            await session.events.drain()
            key = session.playback._crossfade.key
            track = voice.prepared
            assert key is not None and track is not None
            revisions = session.state.revisions
            edits: dict[str, commands.Command] = {
                "remove": commands.Remove(entries[1].id),
                "reorder": commands.Move(
                    entries[2].id, entries[1].id, revisions.queue_revision
                ),
                "disable": commands.Crossfade(0),
                "duration": commands.Crossfade(3),
            }
            # Both messages are already queued when the edit publishes its
            # invalidation. The subscriber's PREPARE command necessarily follows.
            changed = session._accept(Request(uuid4(), edits[edit]))
            session._post(
                CrossfadeReady(key, track)
                if signal == "due"
                else PreparationFailed(key, VoiceError("obsolete"))
            )
            await asyncio.wait_for(asyncio.shield(changed), 2)
            await session.read_status()
            assert session.snapshot.current is not None
            assert session.snapshot.current.id == entries[0].id
            assert len(voice.played) == len(session.snapshot.recently_played) == 1
            assert voice.connected
            assert session.status.last_issue is None
        finally:
            await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancellation_source", ["component", "runner"])
def test_cancelled_operation_rejects_accepted_and_waiting_work_and_still_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancellation_source: str,
) -> None:
    monkeypatch.setattr(session_module, "INBOX_CAPACITY", 1)

    class CancelledConnectVoice(ConnectingVoice):
        async def connect(self, channel_id: int) -> None:
            self.entered.set()
            await self.release.wait()
            raise asyncio.CancelledError("Adapter operation cancelled")

    async def scenario() -> None:
        voice = CancelledConnectVoice()
        session = await Session.create(
            lambda: SQLiteStore(tmp_path / "cancelled-inbox.sqlite3"),
            ControlledResolver(),
            voice,
        )
        operations: list[asyncio.Task[PlayerSnapshot]] = []
        try:
            async with session.subscribe() as events:
                await events.get()
                operations.append(asyncio.create_task(session.connect(7)))
                await asyncio.wait_for(voice.entered.wait(), 2)
                operations.append(asyncio.create_task(session.set_volume(0.2)))
                await _wait_until(lambda: len(session._inbox) == 1)
                operations.append(asyncio.create_task(session.set_volume(0.8)))
                await asyncio.sleep(0)
                assert not any(operation.done() for operation in operations)

                if cancellation_source == "component":
                    voice.release.set()
                else:
                    session._runner.cancel()
                outcomes = await asyncio.wait_for(
                    asyncio.gather(*operations, return_exceptions=True), 2
                )
                assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
                await asyncio.wait_for(asyncio.shield(session._runner), 2)
                assert session._runner.done() and not session._runner.cancelled()
                assert session.state.faulted
                assert not session._inbox
                assert 0.2 not in voice.volumes and 0.8 not in voice.volumes
                assert await asyncio.wait_for(events.get(), 2) is None
                with pytest.raises(RuntimeError, match="operational failure"):
                    await session.read_status()

            disconnects = voice.disconnect_count
            await asyncio.wait_for(session.close(), 2)
            assert voice.disconnect_count == disconnects + 1
            with pytest.raises(RuntimeError, match="closed"):
                await session.state.worker.call(lambda player: player.snapshot)
        finally:
            voice.release.set()
            for operation in operations:
                if not operation.done():
                    operation.cancel()
            await asyncio.gather(*operations, return_exceptions=True)
            try:
                await asyncio.wait_for(session.close(), 2)
            finally:
                # Keep failed regressions isolated even if Session cleanup itself
                # is the failing assertion under investigation.
                await session.state.worker.close()

    asyncio.run(scenario())


def test_cancelled_idle_runner_fails_new_ingress_and_closes_owned_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session, voice = await session_with_gate(tmp_path)
        try:
            async with session.subscribe() as events:
                await events.get()
                session._runner.cancel()
                await asyncio.wait_for(asyncio.shield(session._runner), 2)
                assert session._runner.done() and not session._runner.cancelled()
                assert session.state.faulted
                assert await asyncio.wait_for(events.get(), 2) is None
                with pytest.raises(RuntimeError, match="operational failure"):
                    await session.set_volume(0.2)
                assert 0.2 not in voice.volumes
            await asyncio.wait_for(session.close(), 2)
            assert voice.disconnect_count == 1
            with pytest.raises(RuntimeError, match="closed"):
                await session.state.worker.call(lambda player: player.snapshot)
        finally:
            try:
                await asyncio.wait_for(session.close(), 2)
            finally:
                await session.state.worker.close()

    asyncio.run(scenario())
