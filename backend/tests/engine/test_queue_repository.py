# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.queue import Contributor, QueueEntry, QueueOrigin
from nahormaar_backend.engine.domain.sessions import ListeningSession
from nahormaar_backend.engine.domain.tracks import MediaIdentity, Track
from nahormaar_backend.engine.persistence import (
    Base,
    ListeningSessionRepository,
    QueueRepository,
    TrackRepository,
)

from .database import isolated_database


@pytest.fixture
def track() -> Track:
    created_at = datetime(2026, 9, 21, tzinfo=UTC)
    return Track(
        MediaIdentity("youtube", "GCYGuZGE6DA"),
        "https://www.youtube.com/watch?v=GCYGuZGE6DA",
        TrackMetadata(title="Я хочу любить", artist="Амура"),
        created_at=created_at,
        updated_at=created_at,
    )


def test_repeated_tracks_roundtrip_with_order_attribution_and_session_scope(
    tmp_path: Path, track: Track
) -> None:
    owner, other_owner = uuid4(), uuid4()
    first = QueueEntry(owner, track.id, 0, added_by=Contributor(uuid4(), "Kai", "a3f0"))
    second = QueueEntry(owner, track.id, 1, origin=QueueOrigin.RADIO)
    other = QueueEntry(other_owner, track.id, 0)

    async def scenario() -> None:
        path = tmp_path / "queue.sqlite3"
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                for session_id in (owner, other_owner):
                    await ListeningSessionRepository(session).add(
                        ListeningSession(id=session_id)
                    )
                await TrackRepository(session).add(track)
                queue = QueueRepository(session)
                for entry in (second, other, first):
                    await queue.add(entry)
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                queue = QueueRepository(session)
                assert await queue.entries(owner) == (first, second)
                assert await queue.entries(other_owner) == (other,)
                assert await queue.entries(uuid4()) == ()
                assert await queue.get(owner, first.id) == first
                assert await queue.get(other_owner, first.id) is None
                assert await queue.get(owner, uuid4()) is None
                assert await TrackRepository(session).get(track.id) == track

    asyncio.run(scenario())


def test_metadata_update_preserves_both_queue_occurrences(
    tmp_path: Path, track: Track
) -> None:
    owner = uuid4()
    entries = (QueueEntry(owner, track.id, 0), QueueEntry(owner, track.id, 1))
    updated = replace(
        track, metadata=track.metadata.merge(TrackMetadata(duration_seconds=240))
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "queue.sqlite3") as sessions:
            async with sessions.begin() as session:
                await ListeningSessionRepository(session).add(
                    ListeningSession(id=owner)
                )
                await TrackRepository(session).add(track)
                for entry in entries:
                    await QueueRepository(session).add(entry)
            async with sessions.begin() as session:
                await TrackRepository(session).update(updated)
            async with sessions.begin() as session:
                restored = await QueueRepository(session).entries(owner)
                assert restored == entries
                for entry in restored:
                    assert await TrackRepository(session).get(entry.track_id) == updated

    asyncio.run(scenario())


def test_missing_track_reference_rolls_back_track_and_queue_writes(
    tmp_path: Path, track: Track
) -> None:
    owner = uuid4()
    valid = QueueEntry(owner, track.id, 0)
    invalid = QueueEntry(owner, uuid4(), 1)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "queue.sqlite3") as sessions:
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await ListeningSessionRepository(session).add(
                        ListeningSession(id=owner)
                    )
                    await TrackRepository(session).add(track)
                    await QueueRepository(session).add(valid)
                    await QueueRepository(session).add(invalid)
            async with sessions.begin() as session:
                assert await ListeningSessionRepository(session).get(owner) is None
                assert await TrackRepository(session).get(track.id) is None
                assert await QueueRepository(session).entries(owner) == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("duplicate_id", [False, True])
def test_duplicate_position_or_entry_id_rolls_back_the_batch(
    tmp_path: Path, track: Track, duplicate_id: bool
) -> None:
    owner = uuid4()
    first = QueueEntry(owner, track.id, 0)
    second = (
        replace(first, session_id=uuid4())
        if duplicate_id
        else replace(first, id=uuid4())
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "queue.sqlite3") as sessions:
            async with sessions.begin() as session:
                for session_id in {owner, second.session_id}:
                    await ListeningSessionRepository(session).add(
                        ListeningSession(id=session_id)
                    )
                await TrackRepository(session).add(track)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    queue = QueueRepository(session)
                    await queue.add(first)
                    await queue.add(second)
            async with sessions.begin() as session:
                queue = QueueRepository(session)
                assert await queue.entries(owner) == ()
                assert await queue.entries(second.session_id) == ()
                assert await TrackRepository(session).get(track.id) == track

    asyncio.run(scenario())


def test_removing_one_occurrence_preserves_track_and_other_positions(
    tmp_path: Path, track: Track
) -> None:
    owner = uuid4()
    first, second = QueueEntry(owner, track.id, 0), QueueEntry(owner, track.id, 1)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "queue.sqlite3") as sessions:
            async with sessions.begin() as session:
                await ListeningSessionRepository(session).add(
                    ListeningSession(id=owner)
                )
                await TrackRepository(session).add(track)
                await QueueRepository(session).add(first)
                await QueueRepository(session).add(second)
            async with sessions.begin() as session:
                queue = QueueRepository(session)
                assert not await queue.remove(uuid4(), first.id)
                assert await queue.get(owner, first.id) == first
                assert await queue.remove(owner, first.id)
                assert await queue.get(owner, first.id) is None
                assert not await queue.remove(owner, first.id)
            async with sessions.begin() as session:
                assert await QueueRepository(session).entries(owner) == (second,)
                assert await TrackRepository(session).get(track.id) == track

    asyncio.run(scenario())


def test_referenced_track_cannot_be_deleted(tmp_path: Path, track: Track) -> None:
    entry = QueueEntry(uuid4(), track.id, 0)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "queue.sqlite3") as sessions:
            async with sessions.begin() as session:
                await ListeningSessionRepository(session).add(
                    ListeningSession(id=entry.session_id)
                )
                await TrackRepository(session).add(track)
                await QueueRepository(session).add(entry)
            tracks = Base.metadata.tables["tracks"]
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await session.execute(delete(tracks).where(tracks.c.id == track.id))
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(track.id) == track
                assert (
                    await QueueRepository(session).get(entry.session_id, entry.id)
                    == entry
                )

    asyncio.run(scenario())


def test_rollback_restores_removed_entry_and_discards_replacement(
    tmp_path: Path, track: Track
) -> None:
    original = QueueEntry(uuid4(), track.id, 0)
    new_track = replace(
        track, id=uuid4(), identity=MediaIdentity("youtube", "different")
    )
    replacement = QueueEntry(original.session_id, new_track.id, 0)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "queue.sqlite3") as sessions:
            async with sessions.begin() as session:
                await ListeningSessionRepository(session).add(
                    ListeningSession(id=original.session_id)
                )
                await TrackRepository(session).add(track)
                await QueueRepository(session).add(original)
            with pytest.raises(RuntimeError, match="Abort operation"):
                async with sessions.begin() as session:
                    queue = QueueRepository(session)
                    assert await queue.remove(original.session_id, original.id)
                    await TrackRepository(session).add(new_track)
                    await queue.add(replacement)
                    raise RuntimeError("Abort operation")
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(new_track.id) is None
                assert await TrackRepository(session).get(track.id) == track
                assert await QueueRepository(session).entries(original.session_id) == (
                    original,
                )

    asyncio.run(scenario())


def test_foreign_keys_are_enabled_on_multiple_connections(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "queue.sqlite3") as sessions:
            async with sessions.begin() as first:
                assert await first.scalar(text("PRAGMA foreign_keys")) == 1
                async with sessions.begin() as second:
                    assert await second.scalar(text("PRAGMA foreign_keys")) == 1

    asyncio.run(scenario())
