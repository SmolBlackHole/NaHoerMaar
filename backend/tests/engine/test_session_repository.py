# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.queue import Contributor, QueueEntry, QueueOrigin
from nahormaar_backend.engine.domain.sessions import (
    ListeningSession,
    PlaybackCheckpoint,
    PlaybackEndReason,
    PlaybackIntent,
    PlaybackRecord,
)
from nahormaar_backend.engine.domain.tracks import MediaIdentity, Track
from nahormaar_backend.engine.persistence import (
    Base,
    ListeningSessionRepository,
    PlaybackCheckpointRepository,
    PlaybackRecordRepository,
    QueueRepository,
    TrackRepository,
)

from .database import isolated_database

type PlaybackFixture = tuple[
    ListeningSession, Track, PlaybackRecord, PlaybackCheckpoint
]


@pytest.fixture
def playback() -> PlaybackFixture:
    owner = ListeningSession(channel_id=123456789012345678, volume=0.6)
    track = Track(
        MediaIdentity("youtube", "GCYGuZGE6DA"),
        "https://www.youtube.com/watch?v=GCYGuZGE6DA",
        TrackMetadata(title="Я хочу любить", artist="Амура"),
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
        updated_at=datetime(2026, 9, 21, tzinfo=UTC),
    )
    record = PlaybackRecord(
        owner.id,
        track.id,
        uuid4(),
        datetime(2026, 9, 21, 15, 30, tzinfo=timezone(timedelta(hours=2))),
        added_by=Contributor(uuid4(), "Kai", "abcd"),
        origin=QueueOrigin.RADIO,
    )
    checkpoint = PlaybackCheckpoint(
        owner.id,
        intent=PlaybackIntent.PLAYING,
        entry_id=record.entry_id,
        track_id=track.id,
        play_id=record.id,
        position_seconds=42.125,
        added_by=record.added_by,
        origin=record.origin,
    )
    return owner, track, record, checkpoint


async def store_playback(session: AsyncSession, playback: PlaybackFixture) -> None:
    owner, track, record, checkpoint = playback
    await ListeningSessionRepository(session).add(owner)
    await TrackRepository(session).add(track)
    await PlaybackRecordRepository(session).add(record)
    await PlaybackCheckpointRepository(session).save(checkpoint)


def test_session_history_and_checkpoint_roundtrip_after_reopening(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, track, record, checkpoint = playback

    async def scenario() -> None:
        path = tmp_path / "session.sqlite3"
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                assert await ListeningSessionRepository(session).get(owner.id) == owner
                assert (
                    await PlaybackCheckpointRepository(session).get(owner.id)
                    == checkpoint
                )
                restored = await PlaybackRecordRepository(session).get(
                    owner.id, record.id
                )
                assert restored is not None
                assert restored == record
                assert restored.started_at.tzinfo is UTC
                assert restored.started_at.hour == 13
                assert await TrackRepository(session).get(restored.track_id) == track
                assert (
                    await PlaybackRecordRepository(session).get(uuid4(), record.id)
                    is None
                )
                assert await ListeningSessionRepository(session).get(uuid4()) is None
                assert await PlaybackCheckpointRepository(session).get(uuid4()) is None
                assert await PlaybackRecordRepository(session).recent(uuid4()) == ()

    asyncio.run(scenario())


def test_position_pause_and_reconnect_updates_preserve_one_confirmed_play(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, _, record, checkpoint = playback
    paused = replace(checkpoint, intent=PlaybackIntent.PAUSED, position_seconds=97.75)
    disconnected = replace(owner, channel_id=None, volume=0)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
            async with sessions.begin() as session:
                await PlaybackCheckpointRepository(session).save(paused)
                await ListeningSessionRepository(session).update(disconnected)
            async with sessions.begin() as session:
                assert (
                    await PlaybackCheckpointRepository(session).get(owner.id) == paused
                )
                assert (
                    await ListeningSessionRepository(session).get(owner.id)
                    == disconnected
                )
                assert await PlaybackRecordRepository(session).recent(owner.id) == (
                    record,
                )
            async with sessions.begin() as session:
                await PlaybackCheckpointRepository(session).save(
                    replace(paused, intent=PlaybackIntent.PLAYING, position_seconds=12)
                )
                await ListeningSessionRepository(session).update(owner)
            async with sessions.begin() as session:
                assert await PlaybackRecordRepository(session).recent(owner.id) == (
                    record,
                )

    asyncio.run(scenario())


def test_stopped_and_unconfirmed_checkpoints_do_not_create_history(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, track, _, checkpoint = playback
    stopped = PlaybackCheckpoint(owner.id)
    pending = replace(checkpoint, play_id=None, intent=PlaybackIntent.PAUSED)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await ListeningSessionRepository(session).add(owner)
                await TrackRepository(session).add(track)
                await PlaybackCheckpointRepository(session).save(stopped)
            for value in (stopped, pending, stopped):
                async with sessions.begin() as session:
                    await PlaybackCheckpointRepository(session).save(value)
                async with sessions.begin() as session:
                    assert (
                        await PlaybackCheckpointRepository(session).get(owner.id)
                        == value
                    )
                    assert (
                        await PlaybackRecordRepository(session).recent(owner.id) == ()
                    )
            async with sessions.begin() as session:
                await PlaybackCheckpointRepository(session).clear(owner.id)
                await PlaybackCheckpointRepository(session).clear(owner.id)
            async with sessions.begin() as session:
                assert await PlaybackCheckpointRepository(session).get(owner.id) is None
                assert await ListeningSessionRepository(session).get(owner.id) == owner

    asyncio.run(scenario())


def test_removing_occurrence_keeps_history_checkpoint_and_shared_metadata(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, track, record, checkpoint = playback
    entry = QueueEntry(owner.id, track.id, 0, id=record.entry_id)
    refreshed = replace(track, metadata=replace(track.metadata, title="Updated title"))

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
                await QueueRepository(session).add(entry)
            async with sessions.begin() as session:
                assert await QueueRepository(session).remove(owner.id, entry.id)
                await TrackRepository(session).update(refreshed)
            async with sessions.begin() as session:
                assert await QueueRepository(session).entries(owner.id) == ()
                assert (
                    await PlaybackRecordRepository(session).get(owner.id, record.id)
                    == record
                )
                assert (
                    await PlaybackCheckpointRepository(session).get(owner.id)
                    == checkpoint
                )
                assert await TrackRepository(session).get(record.track_id) == refreshed

    asyncio.run(scenario())


def test_explicit_replay_gets_new_record_and_history_is_scoped_ordered_and_limited(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, _, record, checkpoint = playback
    finished = replace(
        record,
        ended_at=record.started_at + timedelta(seconds=180),
        end_reason=PlaybackEndReason.COMPLETED,
    )
    replay = replace(
        record, id=UUID(int=1), started_at=record.started_at + timedelta(hours=1)
    )
    tied = replace(replay, id=UUID(int=2))
    another_owner = ListeningSession()
    other = replace(replay, id=uuid4(), session_id=another_owner.id)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
                await ListeningSessionRepository(session).add(another_owner)
            async with sessions.begin() as session:
                history = PlaybackRecordRepository(session)
                await history.update(finished)
                await history.add(other)
                await history.add(tied)
                await history.add(replay)
                await PlaybackCheckpointRepository(session).save(
                    replace(checkpoint, play_id=replay.id, position_seconds=0)
                )
            async with sessions.begin() as session:
                history = PlaybackRecordRepository(session)
                assert await history.recent(owner.id) == (tied, replay, finished)
                assert await history.recent(owner.id, limit=2) == (tied, replay)
                assert await history.recent(another_owner.id) == (other,)
                assert await history.get(owner.id, record.id) == finished

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, -1, True])
def test_history_rejects_invalid_limits(tmp_path: Path, limit: int) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                with pytest.raises(ValueError, match="limit"):
                    await PlaybackRecordRepository(session).recent(uuid4(), limit=limit)

    asyncio.run(scenario())


def test_record_start_context_and_finished_outcome_cannot_be_rewritten(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, _, record, _ = playback
    finished = replace(
        record, ended_at=record.started_at, end_reason=PlaybackEndReason.SKIPPED
    )
    mutations = (
        replace(record, track_id=uuid4()),
        replace(record, entry_id=uuid4()),
        replace(record, started_at=record.started_at - timedelta(seconds=1)),
        replace(record, added_by=None),
        replace(record, origin=QueueOrigin.MANUAL),
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
            for changed in mutations:
                with pytest.raises(ValueError, match="start context"):
                    async with sessions.begin() as session:
                        await PlaybackRecordRepository(session).update(changed)
            for changed in (
                replace(record, id=uuid4()),
                replace(record, session_id=uuid4()),
            ):
                with pytest.raises(LookupError):
                    async with sessions.begin() as session:
                        await PlaybackRecordRepository(session).update(changed)
            with pytest.raises(LookupError):
                async with sessions.begin() as session:
                    await ListeningSessionRepository(session).update(ListeningSession())
            async with sessions.begin() as session:
                await PlaybackRecordRepository(session).update(finished)
                await PlaybackRecordRepository(session).update(finished)
            with pytest.raises(ValueError, match="finished"):
                async with sessions.begin() as session:
                    await PlaybackRecordRepository(session).update(record)
            async with sessions.begin() as session:
                assert await PlaybackRecordRepository(session).recent(owner.id) == (
                    finished,
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["session", "entry", "track", "play"])
def test_checkpoint_cannot_reference_an_unrelated_or_missing_play(
    tmp_path: Path, playback: PlaybackFixture, mismatch: str
) -> None:
    owner, track, record, checkpoint = playback
    other_owner = ListeningSession()
    other_track = replace(track, id=uuid4(), identity=MediaIdentity("youtube", "other"))
    invalid = {
        "session": replace(checkpoint, session_id=other_owner.id),
        "entry": replace(checkpoint, entry_id=uuid4()),
        "track": replace(checkpoint, track_id=other_track.id),
        "play": replace(checkpoint, play_id=uuid4()),
    }[mismatch]

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
                await ListeningSessionRepository(session).add(other_owner)
                await TrackRepository(session).add(other_track)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await ListeningSessionRepository(session).update(
                        replace(owner, volume=0)
                    )
                    await PlaybackCheckpointRepository(session).save(invalid)
            async with sessions.begin() as session:
                assert await ListeningSessionRepository(session).get(owner.id) == owner
                assert (
                    await PlaybackCheckpointRepository(session).get(owner.id)
                    == checkpoint
                )
                assert (
                    await PlaybackCheckpointRepository(session).get(other_owner.id)
                    is None
                )
                assert await PlaybackRecordRepository(session).recent(owner.id) == (
                    record,
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["queue", "record", "checkpoint"])
@pytest.mark.parametrize("missing", ["session", "track"])
def test_all_occurrences_require_existing_session_and_track_references(
    tmp_path: Path, playback: PlaybackFixture, kind: str, missing: str
) -> None:
    owner, track, record, checkpoint = playback
    session_id = uuid4() if missing == "session" else owner.id
    track_id = uuid4() if missing == "track" else track.id

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await ListeningSessionRepository(session).add(owner)
                await TrackRepository(session).add(track)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    if kind == "queue":
                        await QueueRepository(session).add(
                            QueueEntry(session_id, track_id, 0)
                        )
                    elif kind == "record":
                        await PlaybackRecordRepository(session).add(
                            replace(record, session_id=session_id, track_id=track_id)
                        )
                    else:
                        await PlaybackCheckpointRepository(session).save(
                            replace(
                                checkpoint,
                                session_id=session_id,
                                track_id=track_id,
                                play_id=None,
                            )
                        )
            async with sessions.begin() as session:
                assert await QueueRepository(session).entries(owner.id) == ()
                assert await PlaybackRecordRepository(session).recent(owner.id) == ()
                assert await PlaybackCheckpointRepository(session).get(owner.id) is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "table_name", ["listening_sessions", "tracks", "playback_records"]
)
def test_recovery_references_protect_parent_rows_from_deletion(
    tmp_path: Path, playback: PlaybackFixture, table_name: str
) -> None:
    owner, _, record, checkpoint = playback

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await session.execute(delete(Base.metadata.tables[table_name]))
            async with sessions.begin() as session:
                assert (
                    await PlaybackCheckpointRepository(session).get(owner.id)
                    == checkpoint
                )
                assert await PlaybackRecordRepository(session).recent(owner.id) == (
                    record,
                )

    asyncio.run(scenario())


def test_duplicate_confirmed_play_id_rolls_back_without_an_extra_listen(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, _, record, checkpoint = playback

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await PlaybackCheckpointRepository(session).clear(owner.id)
                    await PlaybackRecordRepository(session).add(record)
            async with sessions.begin() as session:
                assert await PlaybackRecordRepository(session).recent(owner.id) == (
                    record,
                )
                assert (
                    await PlaybackCheckpointRepository(session).get(owner.id)
                    == checkpoint
                )

    asyncio.run(scenario())


def test_application_failure_rolls_back_every_repository_together(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, track, record, checkpoint = playback
    entry = QueueEntry(owner.id, track.id, 0)
    new_owner = ListeningSession()
    new_track = replace(track, id=uuid4(), identity=MediaIdentity("youtube", "new"))
    ended = replace(
        record, ended_at=record.started_at, end_reason=PlaybackEndReason.STOPPED
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            async with sessions.begin() as session:
                await store_playback(session, playback)
                await QueueRepository(session).add(entry)
            with pytest.raises(RuntimeError, match="Abort operation"):
                async with sessions.begin() as session:
                    await ListeningSessionRepository(session).update(
                        replace(owner, volume=0)
                    )
                    await ListeningSessionRepository(session).add(new_owner)
                    await TrackRepository(session).add(new_track)
                    await QueueRepository(session).remove(owner.id, entry.id)
                    await QueueRepository(session).add(
                        QueueEntry(new_owner.id, new_track.id, 0)
                    )
                    await PlaybackRecordRepository(session).update(ended)
                    await PlaybackCheckpointRepository(session).save(
                        PlaybackCheckpoint(owner.id)
                    )
                    raise RuntimeError("Abort operation")
            async with sessions.begin() as session:
                assert await ListeningSessionRepository(session).get(owner.id) == owner
                assert (
                    await ListeningSessionRepository(session).get(new_owner.id) is None
                )
                assert await TrackRepository(session).get(new_track.id) is None
                assert await QueueRepository(session).entries(owner.id) == (entry,)
                assert await QueueRepository(session).entries(new_owner.id) == ()
                assert await PlaybackRecordRepository(session).recent(owner.id) == (
                    record,
                )
                assert (
                    await PlaybackCheckpointRepository(session).get(owner.id)
                    == checkpoint
                )

    asyncio.run(scenario())


def test_initial_creation_is_not_committed_by_individual_repositories(
    tmp_path: Path, playback: PlaybackFixture
) -> None:
    owner, track, _, _ = playback

    async def scenario() -> None:
        async with isolated_database(tmp_path / "session.sqlite3") as sessions:
            with pytest.raises(RuntimeError, match="Abort operation"):
                async with sessions.begin() as session:
                    await store_playback(session, playback)
                    await QueueRepository(session).add(
                        QueueEntry(owner.id, track.id, 0)
                    )
                    raise RuntimeError("Abort operation")
            async with sessions.begin() as session:
                for table in Base.metadata.sorted_tables:
                    assert (
                        await session.scalar(select(func.count()).select_from(table))
                        == 0
                    )

    asyncio.run(scenario())
