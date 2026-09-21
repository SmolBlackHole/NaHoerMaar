# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend.application.events import (
    PlaybackChanged,
    QueueChanged,
    RadioChanged,
    SessionEvent,
)
from nahormaar_backend.application.session import Session
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.commands import Receipt, Revisions
from nahormaar_backend.domain.models import PlaybackState, PlayerSnapshot, QueueEntry
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError
from test_commands import wait_for
from test_playback import ControlledResolver, FakeVoice

VIDEO = "https://youtu.be/Pqp9fDRp1lw"


@asynccontextmanager
async def running_session(
    path: Path,
) -> AsyncGenerator[tuple[Session, ControlledResolver, FakeVoice]]:
    resolver, voice = ControlledResolver(), FakeVoice()
    session = await Session.create(lambda: SQLiteStore(path), resolver, voice)
    try:
        yield session, resolver, voice
    finally:
        await session.close()


def test_queue_fact_observes_committed_state_revision_and_request_receipt(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "session.sqlite3"
        async with running_session(path) as (session, _, _):
            request_id = uuid4()
            command = commands.Add(VIDEO)
            observed: list[
                tuple[SessionEvent, PlayerSnapshot, Revisions, Receipt | None]
            ] = []

            async def inspect_committed(event: SessionEvent) -> None:
                with SQLiteStore(path) as reader:
                    observed.append(
                        (
                            event,
                            reader.load(),
                            reader.revisions(),
                            reader.reserve(
                                Receipt(request_id, commands.fingerprint(command))
                            ),
                        )
                    )

            session.events.subscribe((QueueChanged,), inspect_committed)
            before = await session.read_status()
            reply = await session.request(request_id, command)
            await session.events.drain()
            assert len(observed) == 1
            event, stored, revisions, receipt = observed[0]
            assert event.before == before
            assert event.after == reply.status
            assert stored == event.after.player
            assert revisions.revision == event.after.revision
            assert revisions.queue_revision == event.after.queue_revision
            assert receipt is not None and receipt.outcome == reply.outcome
            assert len(stored.upcoming) == 1
            assert stored.upcoming[0].id == reply.outcome.entry_id

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure_trigger",
    [
        """CREATE TRIGGER reject_entry BEFORE INSERT ON queue_entries
           BEGIN SELECT RAISE(ABORT, 'injected queue failure'); END""",
        """CREATE TRIGGER reject_state BEFORE UPDATE ON player_state
           BEGIN SELECT RAISE(ABORT, 'injected state failure'); END""",
    ],
)
def test_failed_queue_transaction_emits_no_committed_fact(
    tmp_path: Path, failure_trigger: str
) -> None:
    async def scenario() -> None:
        path = tmp_path / "session.sqlite3"
        async with running_session(path) as (session, _, _):
            original = QueueEntry(VIDEO)
            await session.enqueue(original)
            await session.events.drain()
            before = await session.read_status()
            received: list[SessionEvent] = []

            async def record(event: SessionEvent) -> None:
                received.append(event)

            session.events.subscribe(
                (QueueChanged, PlaybackChanged, RadioChanged), record
            )
            with closing(sqlite3.connect(path, autocommit=True)) as database:
                database.execute(failure_trigger)
            request_id = uuid4()
            command = commands.Add("https://youtu.be/bWHJbIm1TAA")
            with pytest.raises(StorageError):
                await session.request(request_id, command)
            await session.events.drain()
            assert received == []
            with SQLiteStore(path) as reader:
                assert reader.load() == before.player
                assert reader.revisions() == Revisions(
                    before.revision, before.queue_revision
                )
                receipt = reader.reserve(
                    Receipt(request_id, commands.fingerprint(command))
                )
                assert receipt is not None and receipt.outcome is None

    asyncio.run(scenario())


def test_replayed_requests_publish_and_apply_effects_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with running_session(tmp_path / "session.sqlite3") as (session, _, voice):
            received: list[SessionEvent] = []

            async def record(event: SessionEvent) -> None:
                received.append(event)

            session.events.subscribe(
                (QueueChanged, PlaybackChanged, RadioChanged), record
            )
            addition_id, volume_id = uuid4(), uuid4()
            addition, volume = commands.Add(VIDEO), commands.Volume(0.25)
            first = await session.request(addition_id, addition)
            await session.request(volume_id, volume)
            await session.events.drain()
            initial = list(received)
            assert len(initial) == 2
            assert isinstance(initial[0], QueueChanged)
            assert isinstance(initial[1], PlaybackChanged)
            repeated = await asyncio.gather(
                session.request(addition_id, addition),
                session.request(volume_id, volume),
                session.request(addition_id, addition),
            )
            await session.events.drain()
            assert all(reply.replayed for reply in repeated)
            assert repeated[0].outcome == repeated[2].outcome == first.outcome
            assert received == initial
            assert voice.volumes == [0.25]
            assert len(session.snapshot.upcoming) == 1

    asyncio.run(scenario())


def test_optional_subscribers_cannot_block_controls_or_audio_completion(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        async with running_session(tmp_path / "session.sqlite3") as (
            session,
            resolver,
            voice,
        ):
            first, second = (
                QueueEntry(VIDEO),
                QueueEntry("https://youtu.be/bWHJbIm1TAA"),
            )
            await session.enqueue(first)
            await session.enqueue(second)
            await session.connect(7)
            await session.play()
            await wait_for(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await wait_for(lambda: session.snapshot.state is PlaybackState.PLAYING)
            await session.read_status()
            await session.events.drain()
            entered, advanced, cancelled = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )

            async def slow(event: SessionEvent) -> None:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            async def failing(event: SessionEvent) -> None:
                raise RuntimeError("Optional playback observer failed")

            async def observe_next(event: SessionEvent) -> None:
                if event.after.player.current == second:
                    advanced.set()

            blocked = session.events.subscribe((QueueChanged, PlaybackChanged), slow)
            failed = session.events.subscribe((QueueChanged, PlaybackChanged), failing)
            observer = session.events.subscribe((PlaybackChanged,), observe_next)
            async with asyncio.timeout(2):
                await session.set_volume(0.4)
                await entered.wait()
                await session.pause()
                assert (
                    await session.read_status()
                ).player.state is PlaybackState.PAUSED
                await session.play()
                voice.complete(0)
                await advanced.wait()
                state = await session.read_status()
            assert state.player.current == second
            assert state.player.upcoming == ()
            assert state.last_issue is None
            assert voice.pause_count == voice.resume_count == 1
            assert "Optional playback observer failed" in caplog.text
            assert not blocked.closed and not cancelled.is_set()
            disconnects_before_close = voice.disconnect_count
            await session.close()
            assert blocked.closed and failed.closed and observer.closed
            assert cancelled.is_set()
            assert voice.disconnect_count == disconnects_before_close + 1

    asyncio.run(scenario())


def test_unsubscribe_and_session_shutdown_reap_consumer_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with running_session(tmp_path / "session.sqlite3") as (session, _, voice):
            received: list[SessionEvent] = []

            async def record(event: SessionEvent) -> None:
                received.append(event)

            subscription = session.events.subscribe((QueueChanged,), record)
            await session.enqueue(QueueEntry(VIDEO))
            await subscription.drain()
            assert len(received) == 1
            await subscription.close()
            await session.enqueue(QueueEntry(VIDEO))
            await session.events.drain()
            assert len(received) == 1

            entered, cleaned = asyncio.Event(), asyncio.Event()

            async def pending(event: SessionEvent) -> None:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

            active = session.events.subscribe((QueueChanged,), pending)
            await session.enqueue(QueueEntry(VIDEO))
            await entered.wait()
            await session.close()
            await session.events.drain()
            session.events.publish(received[0])
            assert active.closed and subscription.closed and cleaned.is_set()
            assert len(received) == 1
            assert voice.disconnect_count == 1

    asyncio.run(scenario())
