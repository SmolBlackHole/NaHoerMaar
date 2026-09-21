# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nahormaar_backend.engine.domain.catalog import (
    MediaKind,
    MediaReference,
    TrackFinding,
)
from nahormaar_backend.engine.domain.metadata import (
    MetadataKind,
    MetadataSource,
    TrackMetadata,
)
from nahormaar_backend.domain.identity import Contributor
from nahormaar_backend.engine.domain.queue import (
    Add,
    Clear,
    Move,
    QueueOrigin,
    Remove,
    Undo,
)
from nahormaar_backend.engine.domain.tracks import MediaIdentity, Track
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.persistence import (
    ListeningSessionRepository,
    OperationRepository,
    QueueRepository,
)
from nahormaar_backend.engine.session import Session

from .database import isolated_database
from .test_catalog import TIME

ACTOR = Contributor(uuid4(), "Listener", "0abc")


async def tracks_in(sessions: async_sessionmaker[AsyncSession]) -> tuple[Track, ...]:
    return await MetadataStore(sessions, clock=lambda: TIME).remember(
        tuple(
            TrackFinding(
                MediaReference(
                    MediaIdentity("youtube", f"fixture{i:04d}"),
                    MediaKind.TRACK,
                    f"https://youtube.com/watch?v=fixture{i:04d}",
                ),
                TrackMetadata(title=f"Track {i}"),
            )
            for i in range(8)
        ),
        source=MetadataSource("youtube_music", MetadataKind.DISCOVERY, TIME),
    )


def test_concurrent_commands_replay_current_state_and_survive_reopening(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            identifier, operation = uuid4(), uuid4()
            owner = await Session.open(sessions, identifier, clock=lambda: TIME)
            command = Add((tracks[0].id,))
            async with owner.events.subscribe() as events:
                first, *_others = await asyncio.gather(
                    owner.request(operation, command, actor=ACTOR),
                    *(
                        owner.request(uuid4(), Add((tracks[1].id,)), actor=ACTOR)
                        for _ in range(9)
                    ),
                )
                assert len(owner.snapshot.queue.entries) == 10
                assert len({entry.id for entry in owner.snapshot.queue.entries}) == 10
                assert owner.snapshot.settings.queue_revision == 10
                assert events.qsize() == 10
                replay = await owner.request(
                    operation, command, actor=replace(ACTOR, name="Renamed")
                )
                assert replay.replayed and replay.outcome == first.outcome
                assert (
                    replay.snapshot == owner.snapshot
                    and len(replay.snapshot.queue.entries) == 10
                )
                assert events.qsize() == 10
                conflict = await owner.request(
                    operation, Add((tracks[2].id,)), actor=ACTOR
                )
                assert conflict.outcome.code == "idempotency_conflict"
                foreign = await owner.request(
                    operation, command, actor=replace(ACTOR, id=uuid4())
                )
                assert foreign.outcome.code == "idempotency_conflict"
            before = owner.snapshot
            await owner.close()
            restored = await Session.open(sessions, identifier, clock=lambda: TIME)
            assert restored.snapshot == before
            replay = await restored.request(operation, command, actor=ACTOR)
            assert replay.replayed and replay.outcome == first.outcome
            await restored.close()

    asyncio.run(scenario())


def test_manual_priority_duplicates_reorder_conflicts_and_attributed_removal(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            owner = await Session.open(sessions, uuid4(), clock=lambda: TIME)
            other = replace(ACTOR, id=uuid4(), name="Other")
            radio = await owner.request(
                uuid4(), Add((tracks[0].id,), origin=QueueOrigin.RADIO), actor=other
            )
            manual = await owner.request(
                uuid4(), Add((tracks[1].id, tracks[1].id)), actor=ACTOR
            )
            queue = owner.snapshot.queue.entries
            assert tuple(entry.track_id for entry in queue) == (
                tracks[1].id,
                tracks[1].id,
                tracks[0].id,
            )
            duplicate = await owner.request(
                uuid4(),
                Add((tracks[1].id, tracks[2].id, tracks[2].id), skip_duplicates=True),
            )
            assert (
                duplicate.outcome.added_count == 1
                and duplicate.outcome.skipped_count == 2
            )
            stale = await owner.request(
                uuid4(), Move(queue[0].id, None, 0), actor=ACTOR
            )
            assert stale.outcome.code == "queue_conflict"
            revision = owner.snapshot.settings.queue_revision
            moved = await owner.request(
                uuid4(), Move(queue[0].id, None, revision), actor=ACTOR
            )
            assert moved.snapshot.queue.entries[-1].id == queue[0].id
            removed = await owner.request(
                uuid4(),
                Clear(moved.snapshot.settings.queue_revision, ACTOR.id),
                actor=other,
            )
            assert removed.outcome.removed_count == 2
            assert removed.outcome.actor == other
            assert set(entry.id for entry in removed.outcome.entries) == set(
                entry.id for entry in manual.outcome.entries
            )
            assert radio.outcome.entries[0].id in {
                entry.id for entry in owner.snapshot.queue.entries
            }
            await owner.close()

    asyncio.run(scenario())


def test_undo_restores_anchors_without_rewinding_other_edits_and_is_private_single_use(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = TIME
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            owner = await Session.open(sessions, uuid4(), clock=lambda: now)
            added = await owner.request(
                uuid4(), Add(tuple(track.id for track in tracks[:3])), actor=ACTOR
            )
            first, second, third = added.outcome.entries
            removed = await owner.request(uuid4(), Remove(second.id), actor=ACTOR)
            undo_id = removed.outcome.undo_id
            assert (
                undo_id is not None
                and removed.outcome.undo_expires_at == TIME + timedelta(seconds=12)
            )
            newer = await owner.request(uuid4(), Add((tracks[3].id,)), actor=ACTOR)
            denied = await owner.request(
                uuid4(), Undo(undo_id), actor=replace(ACTOR, id=uuid4())
            )
            assert denied.outcome.code == "undo_unavailable"
            now += timedelta(seconds=11)
            undone = await owner.request(uuid4(), Undo(undo_id), actor=ACTOR)
            assert undone.outcome.restored_count == 1
            assert tuple(entry.id for entry in owner.snapshot.queue.entries) == (
                first.id,
                second.id,
                third.id,
                newer.outcome.entries[0].id,
            )
            again = await owner.request(uuid4(), Undo(undo_id), actor=ACTOR)
            assert again.outcome.code == "undo_unavailable"
            expired = await owner.request(uuid4(), Remove(second.id), actor=ACTOR)
            assert expired.outcome.undo_id is not None
            now += timedelta(seconds=12)
            late = await owner.request(
                uuid4(), Undo(expired.outcome.undo_id), actor=ACTOR
            )
            assert late.outcome.code == "undo_unavailable"
            await owner.close()

    asyncio.run(scenario())


def test_failed_commit_has_no_fact_receipt_or_memory_change_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            owner = await Session.open(sessions, uuid4(), clock=lambda: TIME)
            before = owner.snapshot
            operation = uuid4()
            original = ListeningSessionRepository.update

            async def fail(*args: object, **kwargs: object) -> None:
                raise RuntimeError("injected commit failure")

            monkeypatch.setattr(ListeningSessionRepository, "update", fail)
            async with owner.events.subscribe() as events:
                with pytest.raises(RuntimeError, match="injected"):
                    await owner.request(operation, Add((tracks[0].id,)), actor=ACTOR)
                assert events.empty() and owner.snapshot == before
                async with sessions.begin() as database:
                    assert (
                        await QueueRepository(database).entries(before.settings.id)
                        == ()
                    )
                    assert await OperationRepository(database).get(operation) is None
                monkeypatch.setattr(ListeningSessionRepository, "update", original)
                retried = await owner.request(
                    operation, Add((tracks[0].id,)), actor=ACTOR
                )
                assert retried.outcome.code == "ok" and not retried.replayed
                assert events.qsize() == 1
            await owner.close()

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_cancel_accepted_mutation_and_close_drains_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            owner = await Session.open(sessions, uuid4(), clock=lambda: TIME)
            entered, release = asyncio.Event(), asyncio.Event()
            original = OperationRepository.add

            async def hold(
                self: OperationRepository, *args: object, **kwargs: object
            ) -> None:
                entered.set()
                await release.wait()
                await original(self, *args, **kwargs)  # type: ignore[arg-type]

            monkeypatch.setattr(OperationRepository, "add", hold)
            operation = uuid4()
            waiter = asyncio.create_task(
                owner.request(operation, Add((tracks[0].id,)), actor=ACTOR)
            )
            await entered.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            closing = asyncio.create_task(owner.close())
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            await closing
            assert len(owner.snapshot.queue.entries) == 1
            async with sessions.begin() as database:
                assert await OperationRepository(database).get(operation) is not None
            with pytest.raises(RuntimeError, match="closing"):
                await owner.request(uuid4(), Add((tracks[1].id,)))

    asyncio.run(scenario())


def test_concurrent_metadata_refresh_and_queue_writes_do_not_lose_work(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "concurrent.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            owner = await Session.open(sessions, uuid4(), clock=lambda: TIME)
            metadata = MetadataStore(
                sessions, clock=lambda: TIME + timedelta(seconds=1)
            )
            finding = TrackFinding(
                MediaReference(
                    tracks[0].identity, MediaKind.TRACK, tracks[0].source_url
                ),
                TrackMetadata(title="Refreshed"),
            )
            results = await asyncio.gather(
                *(
                    owner.request(uuid4(), Add((track.id,)), actor=ACTOR)
                    for track in tracks
                ),
                *(
                    metadata.remember(
                        (finding,),
                        source=MetadataSource(
                            "youtube", MetadataKind.DETAIL, TIME + timedelta(seconds=i)
                        ),
                    )
                    for i in range(1, 9)
                ),
                return_exceptions=True,
            )
            try:
                assert not [
                    result for result in results if isinstance(result, BaseException)
                ]
                assert len(owner.snapshot.queue.entries) == len(tracks)
            finally:
                await owner.close()

    asyncio.run(scenario())
