# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, insert, select, update

from nahoermaar.catalog.domain import (
    DiscoveryKind,
    MediaKind,
    TrackId,
    TrackSourceId,
)
from nahoermaar.catalog.repository import DiscoveryRepository
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.messaging import MessageBus, MessageContext
from nahoermaar.player.domain import (
    ListeningSessionId,
    MutationOutcome,
    OperationId,
    OperationReceipt,
    PlayerError,
    PlayerAction,
    PlayerErrorCode,
    PlayerState,
    PlaybackIntent,
    RadioSeed,
    RequestOrigin,
)
from nahoermaar.player.events import (
    AddTracks,
    ApplyRadioCandidates,
    CompletePlayback,
    JoinVoice,
    MutationReply,
    Play,
    PlayerChanged,
    PlayerEventStream,
    PlayerRuntimeChange,
    RadioRefillRequested,
    RemoveQueueEntry,
    StartRadio,
    StopRadio,
    TrackSelection,
)
from nahoermaar.player.fsm import transition
from nahoermaar.player.repository import SessionRepository
from nahoermaar.player.session import PlayerSessionManager
from nahoermaar.users.domain import UserId

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 25, 10, tzinfo=UTC)


class NoRadio:
    async def radio(
        self,
        seed: RadioSeed,
        *,
        limit: int,
        continuation: str | None,
    ) -> tuple[tuple[TrackSelection, ...], str | None]:
        raise AssertionError((seed, limit, continuation))


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


async def _seed(
    database: Database,
    *,
    user_id: UserId,
    tracks: tuple[tuple[TrackId, TrackSourceId], ...],
) -> None:
    async with UnitOfWork(database.sessions) as work:
        await work.session.execute(
            insert(Base.metadata.tables["users"]).values(
                id=user_id,
                role="owner",
                access_granted_by=None,
                access_granted_at=None,
                created_at=NOW,
                updated_at=NOW,
                last_login_at=None,
            )
        )
        await work.session.execute(
            insert(Base.metadata.tables["tracks"]),
            [
                {
                    "id": track_id,
                    "title": f"Track {index}",
                    "duration_seconds": 180.0,
                    "artwork_url": None,
                    "album_title": None,
                    "release_date": None,
                    "isrc": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for index, (track_id, _source_id) in enumerate(tracks)
            ],
        )
        await work.session.execute(
            insert(Base.metadata.tables["track_sources"]),
            [
                {
                    "id": source_id,
                    "track_id": track_id,
                    "provider": "youtube",
                    "external_id": f"video{index:06d}",
                    "source_url": (f"https://www.youtube.com/watch?v=video{index:06d}"),
                    "observed_title": f"Track {index}",
                    "observed_artist": None,
                    "observed_duration_seconds": 180.0,
                    "observed_artwork_url": None,
                    "observed_album_title": None,
                    "observed_release_date": None,
                    "observed_isrc": None,
                    "uploader_name": None,
                    "uploader_url": None,
                    "quality": "detail",
                    "availability": "available",
                    "first_seen_at": NOW,
                    "checked_at": NOW,
                }
                for index, (track_id, source_id) in enumerate(tracks)
            ],
        )
        await work.commit()


def test_fsm_keeps_manual_requests_distinct_and_radio_at_target() -> None:
    session_id = ListeningSessionId(uuid4())
    actor = UserId(uuid4())
    tracks = tuple(TrackId(uuid4()) for _ in range(6))
    sources = tuple(TrackSourceId(uuid4()) for _ in range(6))
    state = PlayerState.empty(session_id, NOW)

    added = transition(
        state,
        AddTracks(
            session_id,
            OperationId(uuid4()),
            (
                TrackSelection(tracks[0], sources[0]),
                TrackSelection(tracks[1], sources[1]),
            ),
        ),
        actor,
        NOW,
    )
    assert added.state.session.queue_revision == 1
    assert all(
        entry.request.origin is RequestOrigin.MANUAL
        and entry.request.requested_by == actor
        and entry.request.radio_run_id is None
        for entry in added.state.queue.entries
    )

    started = transition(
        added.state,
        StartRadio(
            session_id,
            OperationId(uuid4()),
            RadioSeed(
                MediaKind.TRACK,
                track_source_id=sources[0],
            ),
        ),
        actor,
        NOW + timedelta(seconds=1),
    )
    run = started.state.radio
    assert run is not None
    assert run.request_id is not None
    refill = next(
        event
        for event in started.events
        if event.__class__.__name__ == "RadioRefillRequested"
    )
    assert hasattr(refill, "request_id")

    applied = transition(
        started.state,
        ApplyRadioCandidates(
            session_id,
            OperationId(uuid4()),
            run.id,
            run.generation,
            run.request_id,
            tuple(
                TrackSelection(track, source)
                for track, source in zip(tracks[2:], sources[2:], strict=True)
            ),
        ),
        None,
        NOW + timedelta(seconds=2),
    )
    assert len(applied.state.queue.entries) == 5
    assert [entry.request.origin for entry in applied.state.queue.entries] == [
        RequestOrigin.MANUAL,
        RequestOrigin.MANUAL,
        RequestOrigin.RADIO,
        RequestOrigin.RADIO,
        RequestOrigin.RADIO,
    ]
    assert all(
        entry.request.requested_by == actor and entry.request.radio_run_id == run.id
        for entry in applied.state.queue.entries[2:]
    )
    assert applied.state.radio is not None
    assert len(applied.state.radio.candidates) == 1

    joined = transition(
        applied.state,
        JoinVoice(session_id, OperationId(uuid4()), 42),
        actor,
        NOW + timedelta(seconds=3),
    )
    playing = transition(
        joined.state,
        Play(session_id, OperationId(uuid4())),
        actor,
        NOW + timedelta(seconds=4),
    )
    assert playing.state.checkpoint.request is not None
    second_manual = transition(
        playing.state,
        CompletePlayback(
            session_id,
            OperationId(uuid4()),
            playing.state.checkpoint.request.id,
        ),
        None,
        NOW + timedelta(seconds=4),
    )
    assert second_manual.state.checkpoint.request is not None
    first_radio = transition(
        second_manual.state,
        CompletePlayback(
            session_id,
            OperationId(uuid4()),
            second_manual.state.checkpoint.request.id,
        ),
        None,
        NOW + timedelta(seconds=5),
    )
    assert first_radio.state.checkpoint.request is not None
    assert first_radio.state.checkpoint.request.origin is RequestOrigin.RADIO
    assert (
        sum(
            entry.request.origin is RequestOrigin.RADIO
            for entry in first_radio.state.queue.entries
        )
        == 3
    )

    next_radio = transition(
        first_radio.state,
        CompletePlayback(
            session_id,
            OperationId(uuid4()),
            first_radio.state.checkpoint.request.id,
        ),
        None,
        NOW + timedelta(seconds=6),
    )
    assert next_radio.state.checkpoint.request is not None
    assert next_radio.state.checkpoint.request.origin is RequestOrigin.RADIO
    assert (
        sum(
            entry.request.origin is RequestOrigin.RADIO
            for entry in next_radio.state.queue.entries
        )
        == 2
    )
    assert any(isinstance(event, RadioRefillRequested) for event in next_radio.events)

    stopped = transition(
        applied.state,
        StopRadio(
            session_id,
            OperationId(uuid4()),
            run.generation,
        ),
        actor,
        NOW + timedelta(seconds=7),
    )
    assert [entry.request.origin for entry in stopped.state.queue.entries] == [
        RequestOrigin.MANUAL,
        RequestOrigin.MANUAL,
    ]
    assert (
        stopped.state.session.queue_revision == applied.state.session.queue_revision + 1
    )
    assert stopped.outcome.action is PlayerAction.RADIO_STOPPED
    assert stopped.outcome.removed_count == 3
    assert stopped.outcome.entry_ids == tuple(
        entry.id for entry in applied.state.queue.entries[2:]
    )
    assert stopped.state.radio is not None
    assert stopped.state.radio.ended_at == NOW + timedelta(seconds=7)

    stopped_while_playing = transition(
        next_radio.state,
        StopRadio(
            session_id,
            OperationId(uuid4()),
            run.generation,
        ),
        actor,
        NOW + timedelta(seconds=7),
    )
    assert stopped_while_playing.state.checkpoint == next_radio.state.checkpoint
    assert stopped_while_playing.state.queue.entries == ()
    assert stopped_while_playing.outcome.removed_count == 2

    legacy_stopped_state = replace(stopped.state, queue=applied.state.queue)
    restarted = transition(
        legacy_stopped_state,
        StartRadio(
            session_id,
            OperationId(uuid4()),
            RadioSeed(MediaKind.TRACK, track_source_id=sources[1]),
        ),
        actor,
        NOW + timedelta(seconds=8),
    )
    assert restarted.outcome.removed_count == 3
    assert [entry.request.origin for entry in restarted.state.queue.entries] == [
        RequestOrigin.MANUAL,
        RequestOrigin.MANUAL,
    ]

    replaced = transition(
        applied.state,
        StartRadio(
            session_id,
            OperationId(uuid4()),
            RadioSeed(MediaKind.TRACK, track_source_id=sources[1]),
            expected_generation=run.generation,
        ),
        actor,
        NOW + timedelta(seconds=8),
    )
    replacement = replaced.state.radio
    assert replacement is not None
    assert replacement.request_id is not None
    assert replacement.id != run.id
    assert replaced.outcome.removed_count == 3
    assert (
        replaced.state.session.queue_revision
        == applied.state.session.queue_revision + 1
    )
    assert [entry.request.origin for entry in replaced.state.queue.entries] == [
        RequestOrigin.MANUAL,
        RequestOrigin.MANUAL,
    ]

    replacement_filled = transition(
        replaced.state,
        ApplyRadioCandidates(
            session_id,
            OperationId(uuid4()),
            replacement.id,
            replacement.generation,
            replacement.request_id,
            tuple(
                TrackSelection(track, source)
                for track, source in zip(tracks[2:5], sources[2:5], strict=True)
            ),
        ),
        None,
        NOW + timedelta(seconds=9),
    )
    assert [
        entry.request.origin for entry in replacement_filled.state.queue.entries
    ] == [
        RequestOrigin.MANUAL,
        RequestOrigin.MANUAL,
        RequestOrigin.RADIO,
        RequestOrigin.RADIO,
        RequestOrigin.RADIO,
    ]
    assert all(
        entry.request.radio_run_id == replacement.id
        for entry in replacement_filled.state.queue.entries[2:]
    )


def test_repository_restores_relational_queue_radio_and_prunes_ephemera() -> None:
    database = _database()
    user_id = UserId(uuid4())
    tracks = tuple((TrackId(uuid4()), TrackSourceId(uuid4())) for _ in range(2))
    _track_id, source_id = tracks[0]

    async def scenario() -> None:
        await _seed(database, user_id=user_id, tracks=tracks)
        session_id = ListeningSessionId(uuid4())
        state = PlayerState.empty(session_id, NOW)
        added = transition(
            state,
            AddTracks(
                session_id,
                OperationId(uuid4()),
                tuple(
                    TrackSelection(candidate_track, candidate_source)
                    for candidate_track, candidate_source in tracks
                ),
            ),
            user_id,
            NOW,
        )
        started = transition(
            added.state,
            StartRadio(
                session_id,
                OperationId(uuid4()),
                RadioSeed(MediaKind.TRACK, source_id),
            ),
            user_id,
            NOW + timedelta(seconds=1),
        )
        removed = transition(
            started.state,
            RemoveQueueEntry(
                session_id,
                OperationId(uuid4()),
                started.state.queue.entries[0].id,
                started.state.session.queue_revision,
            ),
            user_id,
            NOW + timedelta(seconds=2),
        )
        assert removed.save_undo is not None
        sleep_at = NOW + timedelta(minutes=30)
        persisted = replace(
            removed.state,
            session=replace(removed.state.session, sleep_at=sleep_at),
        )

        async with UnitOfWork(database.sessions) as work:
            repository = SessionRepository(work.session)
            await repository.save(persisted)
            await repository.save_undo(removed.save_undo)
            await repository.save_receipt(
                OperationReceipt(
                    OperationId(uuid4()),
                    session_id,
                    "ExpiredTestCommand",
                    b"expired",
                    NOW - timedelta(days=2),
                    NOW - timedelta(days=1),
                    MutationOutcome(PlayerAction.QUEUE_ADDED),
                )
            )
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            repository = SessionRepository(work.session)
            restored = await repository.load(session_id)
            undo = await repository.undo(removed.save_undo.id)
            assert restored is not None
            assert restored.session.sleep_at == sleep_at
            assert restored.radio is not None
            assert restored.radio.seed.track_source_id == source_id
            assert len(restored.queue.entries) == 1
            assert restored.queue.entries[0].track_id == tracks[1][0]
            assert restored.queue.entries[0].request.requested_by == user_id
            assert undo is not None
            assert undo.groups[0].entries[0].request.requested_by == user_id
            await work.session.execute(
                update(Base.metadata.tables["users"])
                .where(Base.metadata.tables["users"].c.id == user_id)
                .values(updated_at=NOW + timedelta(minutes=1))
            )
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            repository = SessionRepository(work.session)
            same = await repository.load(session_id)
            assert same is not None
            assert same.radio is not None
            assert len(same.queue.entries) == 1
            request_count = await work.session.scalar(
                select(func.count()).select_from(Base.metadata.tables["track_requests"])
            )
            queue_count = await work.session.scalar(
                select(func.count()).select_from(Base.metadata.tables["queue_entries"])
            )
            assert request_count == 2
            assert queue_count == 1
            deleted_undos, deleted_receipts = await repository.prune(NOW)
            assert deleted_undos == 0
            assert deleted_receipts == 1
            deleted_undos, deleted_receipts = await repository.prune(
                NOW + timedelta(days=2)
            )
            assert deleted_undos == 1
            assert deleted_receipts == 0
            await work.commit()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_discovery_retention_preserves_snapshot_referenced_by_radio() -> None:
    database = _database()
    user_id = UserId(uuid4())
    track_id = TrackId(uuid4())
    source_id = TrackSourceId(uuid4())

    async def scenario() -> None:
        await _seed(
            database,
            user_id=user_id,
            tracks=((track_id, source_id),),
        )
        async with UnitOfWork(database.sessions) as work:
            discovery = DiscoveryRepository(work.session)
            protected = await discovery.publish(
                kind=DiscoveryKind.PLAYLIST,
                provider_key="youtube_music",
                locator="PLprotected",
                limit=100,
                source_url="https://music.youtube.com/playlist?list=PLprotected",
                playlist_title="Protected radio seed",
                source_ids=(source_id,),
                fetched_at=NOW,
                expires_at=NOW + timedelta(minutes=1),
                source_has_more=False,
                continuation=None,
            )
            await work.commit()

        session_id = ListeningSessionId(uuid4())
        started = transition(
            PlayerState.empty(session_id, NOW),
            StartRadio(
                session_id,
                OperationId(uuid4()),
                RadioSeed(
                    MediaKind.PLAYLIST,
                    discovery_snapshot_id=protected.id,
                ),
            ),
            user_id,
            NOW + timedelta(seconds=1),
        )
        async with UnitOfWork(database.sessions) as work:
            await SessionRepository(work.session).save(started.state)
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            discovery = DiscoveryRepository(work.session)
            for offset in range(1, 5):
                fetched_at = NOW + timedelta(minutes=offset)
                await discovery.publish(
                    kind=DiscoveryKind.PLAYLIST,
                    provider_key="youtube_music",
                    locator="PLprotected",
                    limit=100,
                    source_url="https://music.youtube.com/playlist?list=PLprotected",
                    playlist_title="Protected radio seed",
                    source_ids=(source_id,),
                    fetched_at=fetched_at,
                    expires_at=fetched_at + timedelta(minutes=1),
                    source_has_more=False,
                    continuation=None,
                )
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            preserved = await DiscoveryRepository(work.session).get(protected.id)
            snapshot_count = await work.session.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["discovery_snapshots"]
                )
            )
        assert preserved is not None
        assert snapshot_count == 4

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_mailbox_serializes_commands_and_replays_operation_receipts() -> None:
    database = _database()
    user_id = UserId(uuid4())
    tracks = tuple((TrackId(uuid4()), TrackSourceId(uuid4())) for _ in range(2))

    async def scenario() -> None:
        await _seed(database, user_id=user_id, tracks=tracks)
        bus = MessageBus()
        manager = PlayerSessionManager(
            lambda: UnitOfWork(database.sessions),
            bus,
            NoRadio(),
        )
        bus.subscribe(PlayerChanged, manager.broadcast)
        await manager.start()
        session_id = manager.state.session.id
        commands = tuple(
            AddTracks(
                session_id,
                OperationId(uuid4()),
                (TrackSelection(track_id, source_id),),
            )
            for track_id, source_id in tracks
        )
        context = MessageContext(actor_id=user_id)
        async with manager.events.subscribe(capacity=1) as changes:
            first, second = await asyncio.gather(
                manager.execute(commands[0], context),
                manager.execute(commands[1], context),
            )
            assert await changes.get() is None
        assert first.state.session.revision == 1
        assert second.state.session.revision == 2
        assert [entry.position for entry in manager.state.queue.entries] == [0, 1]

        replay = await manager.execute(commands[0], context)
        assert replay.replayed
        assert replay.outcome == first.outcome
        assert manager.state.session.revision == 2

        conflicting = AddTracks(
            session_id,
            commands[0].operation_id,
            (TrackSelection(tracks[1][0], tracks[1][1]),),
        )
        try:
            await manager.execute(conflicting, context)
        except PlayerError as error:
            assert error.code is PlayerErrorCode.IDEMPOTENCY_CONFLICT
        else:
            raise AssertionError("Changed idempotent command was accepted.")
        await manager.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_start_normalizes_a_disconnected_playing_checkpoint_once() -> None:
    database = _database()
    user_id = UserId(uuid4())
    track_id = TrackId(uuid4())
    source_id = TrackSourceId(uuid4())

    async def scenario() -> None:
        await _seed(
            database,
            user_id=user_id,
            tracks=((track_id, source_id),),
        )
        session_id = ListeningSessionId(uuid4())
        added = transition(
            PlayerState.empty(session_id, NOW),
            AddTracks(
                session_id,
                OperationId(uuid4()),
                (TrackSelection(track_id, source_id),),
            ),
            user_id,
            NOW,
        )
        joined = transition(
            added.state,
            JoinVoice(session_id, OperationId(uuid4()), 42),
            user_id,
            NOW + timedelta(seconds=1),
        )
        playing = transition(
            joined.state,
            Play(session_id, OperationId(uuid4())),
            user_id,
            NOW + timedelta(seconds=2),
        )
        legacy = replace(
            playing.state,
            session=replace(playing.state.session, channel_id=None),
            checkpoint=replace(playing.state.checkpoint, position_seconds=37.5),
        )
        original_revision = legacy.session.revision
        async with UnitOfWork(database.sessions) as work:
            await SessionRepository(work.session).save(legacy)
            await work.commit()

        first = PlayerSessionManager(
            lambda: UnitOfWork(database.sessions),
            MessageBus(),
            NoRadio(),
        )
        await first.start()
        assert first.state.session.revision == original_revision + 1
        assert first.state.session.queue_revision == legacy.session.queue_revision
        assert first.state.session.channel_id is None
        assert first.state.checkpoint.intent is PlaybackIntent.PAUSED
        assert first.state.checkpoint.request == legacy.checkpoint.request
        assert first.state.checkpoint.position_seconds == 37.5
        normalized_revision = first.state.session.revision
        await first.close()

        async with UnitOfWork(database.sessions) as work:
            persisted = await SessionRepository(work.session).load(session_id)
        assert persisted is not None
        assert persisted.session.revision == normalized_revision
        assert persisted.checkpoint.intent is PlaybackIntent.PAUSED

        second = PlayerSessionManager(
            lambda: UnitOfWork(database.sessions),
            MessageBus(),
            NoRadio(),
        )
        await second.start()
        assert second.state.session.revision == normalized_revision
        assert second.state.checkpoint.intent is PlaybackIntent.PAUSED
        await second.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_runtime_refresh_reuses_the_committed_state_without_a_fake_revision() -> None:
    async def scenario() -> None:
        stream = PlayerEventStream()
        state = PlayerState.empty(ListeningSessionId(uuid4()), NOW)
        context = MessageContext()
        async with stream.subscribe() as changes:
            stream.refresh(context, state)
            update = await changes.get()
        assert isinstance(update, PlayerRuntimeChange)
        assert update.context is context
        assert update.state is state

    asyncio.run(scenario())


class StaticRadio:
    def __init__(self, selections: tuple[TrackSelection, ...]) -> None:
        self.selections = selections
        self.calls = 0

    async def radio(
        self,
        seed: RadioSeed,
        *,
        limit: int,
        continuation: str | None,
    ) -> tuple[tuple[TrackSelection, ...], str | None]:
        assert seed.kind is MediaKind.TRACK
        assert limit == 20
        assert continuation is None
        self.calls += 1
        return self.selections, None


def test_bus_commits_radio_then_refills_through_the_session_mailbox() -> None:
    database = _database()
    user_id = UserId(uuid4())
    tracks = tuple((TrackId(uuid4()), TrackSourceId(uuid4())) for _ in range(4))

    async def scenario() -> None:
        await _seed(database, user_id=user_id, tracks=tracks)
        bus = MessageBus()
        radio = StaticRadio(
            tuple(
                TrackSelection(track_id, source_id)
                for track_id, source_id in tracks[1:]
            )
        )
        manager = PlayerSessionManager(
            lambda: UnitOfWork(database.sessions),
            bus,
            radio,
        )

        async def start(
            command: StartRadio,
            context: MessageContext,
        ) -> MutationReply:
            return await manager.execute(command, context)

        async def apply(
            command: ApplyRadioCandidates,
            context: MessageContext,
        ) -> MutationReply:
            return await manager.execute(command, context)

        bus.register_command(StartRadio, start)
        bus.register_command(ApplyRadioCandidates, apply)
        bus.subscribe(RadioRefillRequested, manager.refill)
        await manager.start()
        await bus.execute(
            StartRadio(
                manager.state.session.id,
                OperationId(uuid4()),
                RadioSeed(MediaKind.TRACK, tracks[0][1]),
            ),
            MessageContext(actor_id=user_id),
        )

        for _ in range(100):
            if len(manager.state.queue.entries) == 3:
                break
            await asyncio.sleep(0.01)
        assert radio.calls == 1
        assert len(manager.state.queue.entries) == 3
        assert all(
            entry.request.origin is RequestOrigin.RADIO
            and entry.request.requested_by == user_id
            for entry in manager.state.queue.entries
        )
        await manager.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())
